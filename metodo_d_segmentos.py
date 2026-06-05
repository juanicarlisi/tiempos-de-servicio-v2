"""
Método D — Tiempos de Servicio: Paquetería, Postal y Salud
===========================================================
Procesa el dataset de stops completos (todos los stops de cada transporte,
sin filtrar por segmento) y aplica el Método D para estimar t_serv por celda
(Sucursal x Segmento).

Dataset requerido
-----------------
Origen: query SQL "QUERY TS SUCURSALES DENSAS - TODOS LOS STOPS.sql"
Link CSV: [INSERTAR LINK DE GOOGLE DRIVE]

El CSV tiene 22 columnas separadas por ';', sin encabezado:
  col  0  Sucursal_Key
  col  1  Sucursal_Descripcion
  col  2  Cliente_Key
  col  3  Cliente_Descripcion
  col  4  Ruta
  col  5  Ruta_Descripcion
  col  6  Conductor_Key
  col  7  Transporte
  col  8  Equipo_Pieza
  col  9  Tipo_Equipo_Pieza
  col 10  Parada_Codigo
  col 11  Fecha_Hora_POD
  col 12  Geo_Referencia_Cliente
  col 13  Geo_Referencia_POD
  col 14  Motivo_Key
  col 15  Motivo_Descripcion
  col 16  Codigo_Postal
  col 17  Tipo_Envio
  col 18  Clase_Transporte_Descripcion
  col 19  Acreedor_Key
  col 20  Material_Proyecto
  col 21  Segmento_Proyecto  ← 'Paquetería' | 'Postal' | 'Salud' | 'Otro'

Uso
---
    python metodo_d_segmentos.py

Salida
------
    metodo_d_resultados_segmentos.csv  — tabla con P50, P75, CV, T_rec
    metodo_d_resumen_segmentos.txt     — resumen legible
"""

import csv
import math
import sys
from collections import defaultdict
from datetime import datetime

# ─── Configuración ───────────────────────────────────────────────────────────

INPUT_FILE  = r"DATASET 2025 SUCURSALES DENSAS - TODOS LOS STOPS.csv"
OUTPUT_CSV  = r"metodo_d_resultados_segmentos.csv"
OUTPUT_TXT  = r"metodo_d_resumen_segmentos.txt"

DIST_MAX_D  = 400    # m   — distancia máxima del par
VEL_FIJA_D  = 10.0   # km/h — velocidad fija manejo urbano corto
MIN_OBS_D   = 30     # pares mínimos por celda para reportar
DELTA_T_MIN = 0.5    # min  — tiempo mínimo entre PODs
DELTA_T_MAX = 90.0   # min  — tiempo máximo entre PODs
VEL_MAX     = 60.0   # km/h — filtro velocidad implícita
T_SERV_MIN  = 0.1    # min  — t_serv mínimo para conservar el par

# Sucursales y segmentos a analizar.
# Solo se reportan pares donde la parada origen es (Sucursal, Segmento) objetivo.
# Todos los stops del transporte se cargan al buffer para completar la secuencia.
SUCURSALES_OBJETIVO = {
    "Paquetería": ["Sarandí", "Cordoba (Circunvalacion)", "Rosario"],
    "Postal":     ["Cordoba (Circunvalacion)", "Rosario"],
    "Salud":      ["Iriarte", "WH Salta Salud"],
}

# Índices de columnas (0-based, separador ';')
COL_SUC_DESC   = 1
COL_TRANSPORTE = 7
COL_PARADA     = 10
COL_FECHA_POD  = 11
COL_GPS_POD    = 13
COL_SEGMENTO   = 21

# ─── Utilidades ──────────────────────────────────────────────────────────────

def parse_coords(s):
    s = s.strip()
    if not s:
        return None
    parts = s.split(',')
    if len(parts) != 2:
        return None
    try:
        lat, lon = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    if not (-60 < lat < -20) or not (-75 < lon < -50):
        return None
    return (lat, lon)


def haversine_m(p1, p2):
    R = 6371000
    lat1, lon1 = math.radians(p1[0]), math.radians(p1[1])
    lat2, lon2 = math.radians(p2[0]), math.radians(p2[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def parse_timestamp(s):
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s[:26], fmt)
        except ValueError:
            continue
    return None


def percentile(data, p):
    if not data:
        return None
    d = sorted(data)
    idx = (len(d) - 1) * p / 100
    lo = int(idx)
    hi = lo + 1
    if hi >= len(d):
        return d[-1]
    return d[lo] * (1 - idx + lo) + d[hi] * (idx - lo)


def cv(data):
    if len(data) < 2:
        return None
    mean = sum(data) / len(data)
    if mean == 0:
        return None
    import statistics
    return statistics.stdev(data) / mean

# ─── Procesamiento de un transporte ─────────────────────────────────────────

def process_transport(stops):
    """
    Recibe lista de paradas de un transporte (ya deduplicadas por Parada_Codigo).
    Devuelve lista de (suc, seg, t_serv_D) para pares válidos según Método D.
    """
    if len(stops) < 2:
        return []

    # Ordenar por timestamp real (orden efectivo de visita)
    stops.sort(key=lambda x: (x['ts'] or datetime.max, x['parada']))

    # Excluir transportes batch: si >50% de pares tienen delta_t < DELTA_T_MIN
    tot = fast = 0
    for i in range(len(stops) - 1):
        a, b = stops[i], stops[i + 1]
        if a['ts'] and b['ts']:
            dt = (b['ts'] - a['ts']).total_seconds() / 60
            if dt >= 0:
                tot += 1
                if dt < DELTA_T_MIN:
                    fast += 1
    if tot > 0 and fast / tot > 0.5:
        return []

    # Excluir última parada (regreso al depósito)
    activos = stops[:-1]

    resultados = []
    for i in range(len(activos) - 1):
        a, b = activos[i], activos[i + 1]

        if not (a['gps'] and b['gps'] and a['ts'] and b['ts']):
            continue

        dt = (b['ts'] - a['ts']).total_seconds() / 60
        if dt < 0:
            continue
        if not (DELTA_T_MIN <= dt <= DELTA_T_MAX):
            continue

        d = haversine_m(a['gps'], b['gps'])
        if d > 0 and (d / 1000) / (dt / 60) > VEL_MAX:
            continue
        if d >= DIST_MAX_D:
            continue

        t_manejo = (d / 1000) / VEL_FIJA_D * 60
        t_serv = dt - t_manejo
        if t_serv <= T_SERV_MIN:
            continue

        resultados.append((a['suc'], a['seg'], t_serv))

    return resultados


def is_objetivo(suc, seg):
    return suc in SUCURSALES_OBJETIVO.get(seg, [])

# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print(f"Input : {INPUT_FILE}")
    print(f"Params: DIST_MAX={DIST_MAX_D}m | VEL={VEL_FIJA_D}km/h | MIN_OBS={MIN_OBS_D}")
    print()

    todas_sucursales = set(s for lst in SUCURSALES_OBJETIVO.values() for s in lst)
    resultados = defaultdict(list)
    buffer = defaultdict(list)
    stops_vistos = set()
    total_rows = total_pares = 0

    def flush_transport(tid):
        nonlocal total_pares
        stops = buffer.pop(tid, [])
        if len(stops) < 2:
            return
        if not any(is_objetivo(s['suc'], s['seg']) for s in stops):
            return
        for suc, seg, t in process_transport(stops):
            if is_objetivo(suc, seg):
                resultados[(suc, seg)].append(t)
                total_pares += 1

    with open(INPUT_FILE, encoding='utf-8-sig', errors='replace', newline='') as f:
        reader = csv.reader(f, delimiter=';')
        for row in reader:
            if len(row) < 22:
                continue
            total_rows += 1

            if total_rows % 500_000 == 0:
                print(f"  {total_rows:,} filas | {total_pares:,} pares | {len(buffer):,} transportes en buffer")
                sys.stdout.flush()

            suc = row[COL_SUC_DESC].strip()
            if suc not in todas_sucursales:
                continue

            tid = row[COL_TRANSPORTE].strip()
            try:
                par = int(row[COL_PARADA].strip())
            except ValueError:
                par = 9999

            stop_key = (tid, par)
            if stop_key in stops_vistos:
                continue
            stops_vistos.add(stop_key)

            seg = row[COL_SEGMENTO].strip()
            ts  = parse_timestamp(row[COL_FECHA_POD])
            gps = parse_coords(row[COL_GPS_POD])

            buffer[tid].append({'suc': suc, 'seg': seg, 'parada': par, 'ts': ts, 'gps': gps})

    print(f"\nFlush final de {len(buffer):,} transportes...")
    for tid in list(buffer.keys()):
        flush_transport(tid)

    print(f"\nFilas procesadas : {total_rows:,}")
    print(f"Pares Método D   : {total_pares:,}")

    # Agregar y guardar
    rows_out = []
    for (suc, seg), v in sorted(resultados.items()):
        n = len(v)
        p50    = round(percentile(v, 50), 3) if n >= MIN_OBS_D else None
        p75    = round(percentile(v, 75), 3) if n >= MIN_OBS_D else None
        cv_val = round(cv(v), 3)             if n >= MIN_OBS_D else None
        if p50 is None:
            t_rec = None
            status = f"INSUF (n={n})"
        else:
            t_rec  = p75 if (cv_val and cv_val > 0.8) else p50
            status = "OK"
        rows_out.append({
            'Sucursal': suc, 'Segmento': seg, 'N_pares': n, 'Status': status,
            'P50_min': p50 or '', 'P75_min': p75 or '',
            'CV': cv_val or '', 'T_rec_min': round(t_rec, 3) if t_rec else ''
        })

    with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['Sucursal','Segmento','N_pares','Status','P50_min','P75_min','CV','T_rec_min'])
        w.writeheader(); w.writerows(rows_out)

    lines = [
        "=" * 70,
        "MÉTODO D — Tiempos de Servicio: Paquetería / Postal / Salud",
        f"Params: DIST_MAX={DIST_MAX_D}m | VEL={VEL_FIJA_D}km/h | MIN_OBS={MIN_OBS_D}",
        "=" * 70, ""
    ]
    for seg in ["Paquetería", "Postal", "Salud"]:
        seg_rows = [r for r in rows_out if r['Segmento'] == seg]
        if not seg_rows:
            continue
        lines.append(f"── {seg.upper()} ──")
        lines.append(f"{'Sucursal':<30} {'N pares':>10} {'P50':>8} {'P75':>8} {'CV':>7} {'T_rec':>8} Status")
        lines.append("-" * 78)
        for r in sorted(seg_rows, key=lambda x: -x['N_pares']):
            lines.append(f"{r['Sucursal']:<30} {r['N_pares']:>10,} "
                         f"{str(r['P50_min']):>8} {str(r['P75_min']):>8} "
                         f"{str(r['CV']):>7} {str(r['T_rec_min']):>8}  {r['Status']}")
        lines.append("")

    lines += ["Nota: T_rec = P75 si CV > 0.8, P50 si CV ≤ 0.8", "      INSUF = menos de 30 pares válidos"]
    txt = "\n".join(lines)
    with open(OUTPUT_TXT, 'w', encoding='utf-8') as f:
        f.write(txt)

    print(f"\nCSV  → {OUTPUT_CSV}")
    print(f"TXT  → {OUTPUT_TXT}")
    print(); print(txt)


if __name__ == "__main__":
    main()
