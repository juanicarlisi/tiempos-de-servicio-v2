"""
Método D — Tiempos de Servicio: Automotriz
==========================================
Procesa el dataset de transportes mixtos (todos los stops de los transportes
que contienen entregas RENAULT o PRESTIGE AUTO) y aplica el Método D para
estimar t_serv en el segmento Automotriz.

Por qué transportes mixtos
--------------------------
Los transportes que realizan entregas Automotriz también hacen otras entregas
en el mismo recorrido. Si solo se traen los stops de RENAULT/PRESTIGE, los pares
quedan incompletos y el estimador se infla significativamente. La query SQL
identifica los Transporte IDs con al menos un stop de dichos clientes y trae
TODOS los stops de esos transportes. Los stops de otros clientes se usan
únicamente para completar la secuencia real del recorrido.

Dataset requerido
-----------------
Origen: query SQL "QUERY AUTOMOTRIZ 2026 - TODOS LOS STOPS DE TRANSPORTES MIXTOS.sql"
Link CSV: [INSERTAR LINK DE GOOGLE DRIVE]

El CSV tiene 18 columnas separadas por ';', sin encabezado:
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
  col 17  Es_Automotriz   ← 'Automotriz' | 'Otro'

Nota sobre scans batch
----------------------
Los POD de Automotriz se registran en lote: todos los ítems de una parada
comparten el mismo timestamp. El script deduplica por (Transporte, Parada_Codigo)
para tratar cada stop físico como una sola observación.

Uso
---
    python metodo_d_automotriz.py

Salida
------
    metodo_d_resultados_automotriz.csv
    metodo_d_resumen_automotriz.txt
"""

import csv
import math
import sys
import statistics
from collections import defaultdict
from datetime import datetime

# ─── Configuración ───────────────────────────────────────────────────────────

INPUT_FILE  = r"DATASET 2026 AUTOMOTRIZ - TS.csv"
OUTPUT_CSV  = r"metodo_d_resultados_automotriz.csv"
OUTPUT_TXT  = r"metodo_d_resumen_automotriz.txt"

DIST_MAX_D  = 400    # m   — distancia máxima del par
VEL_FIJA_D  = 10.0   # km/h — velocidad fija manejo urbano corto
MIN_OBS_D   = 30     # pares mínimos por celda
DELTA_T_MIN = 0.5    # min
DELTA_T_MAX = 90.0   # min
VEL_MAX     = 60.0   # km/h
T_SERV_MIN  = 0.1    # min

# Sucursales objetivo para Automotriz
SUCURSALES_OBJETIVO = ["Sarandí", "Cordoba (Circunvalacion)", "Rosario", "Plaza Logistica", "CBN I"]

# Índices de columnas
COL_SUC_DESC   = 1
COL_TRANSPORTE = 7
COL_PARADA     = 10
COL_FECHA_POD  = 11
COL_GPS_POD    = 13
COL_ES_AUTO    = 17   # 'Automotriz' o 'Otro'

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
    return statistics.stdev(data) / mean

# ─── Procesamiento de un transporte ─────────────────────────────────────────

def process_transport(stops):
    """
    Recibe lista de paradas de un transporte (deduplicadas por Parada_Codigo).
    Incluye stops de otros clientes ('Otro') que completan la secuencia.
    Solo reporta t_serv para pares donde la parada origen es 'Automotriz'.
    """
    if len(stops) < 2:
        return []

    stops.sort(key=lambda x: (x['ts'] or datetime.max, x['parada']))

    # Exclusión de transportes batch
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

    # Excluir última parada
    activos = stops[:-1]

    resultados = []
    for i in range(len(activos) - 1):
        a, b = activos[i], activos[i + 1]

        if a['seg'] != 'Automotriz':
            continue

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

        resultados.append((a['suc'], t_serv))

    return resultados

# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print(f"Input : {INPUT_FILE}")
    print(f"Params: DIST_MAX={DIST_MAX_D}m | VEL={VEL_FIJA_D}km/h | MIN_OBS={MIN_OBS_D}")
    print()

    resultados = defaultdict(list)
    buffer = defaultdict(list)
    stops_vistos = set()
    total_rows = total_pares = 0

    def flush_transport(tid):
        nonlocal total_pares
        stops = buffer.pop(tid, [])
        if len(stops) < 2:
            return
        if not any(s['seg'] == 'Automotriz' for s in stops):
            return
        for suc, t in process_transport(stops):
            resultados[suc].append(t)
            total_pares += 1

    with open(INPUT_FILE, encoding='utf-8-sig', errors='replace', newline='') as f:
        reader = csv.reader(f, delimiter=';')
        for row in reader:
            if len(row) < 18:
                continue
            total_rows += 1

            if total_rows % 100_000 == 0:
                print(f"  {total_rows:,} filas | {total_pares:,} pares | {len(buffer):,} transportes")
                sys.stdout.flush()

            suc = row[COL_SUC_DESC].strip()
            if suc not in SUCURSALES_OBJETIVO:
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

            seg = row[COL_ES_AUTO].strip()   # 'Automotriz' o 'Otro'
            ts  = parse_timestamp(row[COL_FECHA_POD])
            gps = parse_coords(row[COL_GPS_POD])

            buffer[tid].append({'suc': suc, 'seg': seg, 'parada': par, 'ts': ts, 'gps': gps})

    print(f"\nFlush final de {len(buffer):,} transportes...")
    for tid in list(buffer.keys()):
        flush_transport(tid)

    print(f"\nFilas procesadas : {total_rows:,}")
    print(f"Pares Método D   : {total_pares:,}")

    # Agregar
    rows_out = []
    all_v = []
    for suc, v in sorted(resultados.items(), key=lambda x: -len(x[1])):
        n = len(v)
        if n < 10:
            continue
        p50    = round(percentile(v, 50), 3)
        p75    = round(percentile(v, 75), 3)
        cv_val = round(cv(v), 3) if cv(v) else None
        t_rec  = p75 if (cv_val and cv_val > 0.8) else p50
        status = "OK" if n >= MIN_OBS_D else f"INSUF (n={n})"
        conf   = "Alta" if n >= 200 else ("Media" if n >= 30 else "Baja")
        rows_out.append({'Sucursal': suc, 'N_pares': n, 'Status': status,
                         'P50_min': p50, 'P75_min': p75, 'CV': cv_val or '',
                         'T_rec_min': round(t_rec, 3), 'Confianza': conf})
        if n >= MIN_OBS_D:
            all_v += v

    # Combinado
    if all_v:
        n = len(all_v)
        p50c    = round(percentile(all_v, 50), 3)
        p75c    = round(percentile(all_v, 75), 3)
        cvc     = round(cv(all_v), 3) if cv(all_v) else None
        t_recc  = p75c if (cvc and cvc > 0.8) else p50c
        rows_out.append({'Sucursal': 'COMBINADO', 'N_pares': n, 'Status': 'OK',
                         'P50_min': p50c, 'P75_min': p75c, 'CV': cvc or '',
                         'T_rec_min': round(t_recc, 3), 'Confianza': 'Media'})

    with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8') as f:
        fields = ['Sucursal','N_pares','Status','P50_min','P75_min','CV','T_rec_min','Confianza']
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(rows_out)

    lines = [
        "=" * 70,
        "MÉTODO D — Tiempos de Servicio: Automotriz",
        f"Params: DIST_MAX={DIST_MAX_D}m | VEL={VEL_FIJA_D}km/h | MIN_OBS={MIN_OBS_D}",
        "=" * 70, "",
        f"{'Sucursal':<30} {'N pares':>8} {'P50':>7} {'P75':>7} {'CV':>7} {'T_rec':>7} {'Conf':>8} Status",
        "-" * 80
    ]
    for r in rows_out:
        marker = " ← combinado" if r['Sucursal'] == 'COMBINADO' else ""
        lines.append(f"{r['Sucursal']:<30} {r['N_pares']:>8,} "
                     f"{str(r['P50_min']):>7} {str(r['P75_min']):>7} "
                     f"{str(r['CV']):>7} {str(r['T_rec_min']):>7}  "
                     f"{r['Confianza']:>8}  {r['Status']}{marker}")
    lines += [
        "",
        "Notas:",
        "  T_rec = P75 si CV > 0.8, P50 si CV ≤ 0.8",
        "  INSUF = menos de 30 pares válidos",
        "  Transportes batch excluidos (>50% pares con delta_t < 0.5 min)",
    ]
    txt = "\n".join(lines)
    with open(OUTPUT_TXT, 'w', encoding='utf-8') as f:
        f.write(txt)

    print(f"\nCSV  → {OUTPUT_CSV}")
    print(f"TXT  → {OUTPUT_TXT}")
    print(); print(txt)


if __name__ == "__main__":
    main()
