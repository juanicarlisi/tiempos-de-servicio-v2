# Tiempos de Servicio por Parada — Método D

Estudio de tiempos de servicio por segmento y sucursal para OCASA, usando el **Método D** de estimación a partir de pares de PODs consecutivos con velocidad fija.

---

## Contexto

El Método D estima el tiempo que un chofer pasa en cada parada de entrega a partir de los scans de POD consecutivos dentro del mismo transporte. La fórmula descuenta el tiempo de manejo vehicular usando una velocidad fija conservadora (10 km/h), lo que funciona bien para pares de paradas cercanas (< 400 m) en zonas urbanas densas.

```
t_serv_D = max(0, delta_t − (distancia_km / 10) × 60)
```

Fue desarrollado para superar los Métodos B y C, que estimaban velocidad por zona H3 y propagaban ese error al estimador de t_serv.

---

## Estructura del proyecto

```
├── metodo_d_segmentos.py       # Paquetería, Postal, Salud (dataset 2025)
├── metodo_d_automotriz.py      # Automotriz (dataset 2026)
├── requirements.txt
├── tiempos_de_servicio_metodo_d.html   # Reporte visual final
└── sql/
    ├── QUERY TS SUCURSALES DENSAS - TODOS LOS STOPS.sql
    └── QUERY AUTOMOTRIZ 2026 - TODOS LOS STOPS DE TRANSPORTES MIXTOS.sql
```

---

## Datos

Los datasets CSV **no están incluidos en el repositorio** por su tamaño. Están disponibles en Google Drive:

| Dataset | Script | Link |
|---|---|---|
| `DATASET 2025 SUCURSALES DENSAS - TODOS LOS STOPS.csv` | `metodo_d_segmentos.py` | https://drive.google.com/file/d/1_8j6bz2FmyTy_SjvaxKyhoAt1QR-p8lX/view?usp=drive_link |
| `DATASET 2026 AUTOMOTRIZ - TS.csv` | `metodo_d_automotriz.py` | https://drive.google.com/file/d/1W8vAyVMm-ge3H7TS1VG9_0Yoxt_zOPF1/view?usp=drive_link |

Descargá los CSV y colocalos en la **misma carpeta** donde estén los scripts Python antes de ejecutar.

### Origen de los datos

Los CSV se generan corriendo las queries SQL contra el DW de OCASA (`[DW].[Fact_POD_Online]`). Las queries están en la carpeta `/sql/`.

**Paquetería / Postal / Salud:**  
`QUERY TS SUCURSALES DENSAS - TODOS LOS STOPS.sql`  
Trae todos los stops de las sucursales objetivo (Sarandí, Córdoba, Rosario, Iriarte, WH Salta Salud) para 2025. El filtro por `Material_Proyecto` fue removido intencionalmente para incluir todos los stops de cada transporte y evitar huecos en la secuencia de paradas.

**Automotriz:**  
`QUERY AUTOMOTRIZ 2026 - TODOS LOS STOPS DE TRANSPORTES MIXTOS.sql`  
Identifica los Transporte IDs que incluyen al menos una entrega de RENAULT S.A. o PRESTIGE AUTO S.A.U., y trae todos los stops de esos transportes (no solo los Automotriz). Esto es esencial porque los transportes son mixtos y sin los stops de otros clientes la secuencia queda incompleta.

---

## Instalación

No se requieren dependencias externas. Solo Python 3.8 o superior.

```bash
git clone <repo>
cd <repo>
# Colocar los CSV según la tabla de datos arriba
python metodo_d_segmentos.py   # Para Paquetería, Postal y Salud
python metodo_d_automotriz.py  # Para Automotriz
```

---

## Parámetros del Método D

| Parámetro | Valor | Descripción |
|---|---|---|
| `DIST_MAX_D` | 400 m | Distancia máxima entre paradas del par |
| `VEL_FIJA_D` | 10 km/h | Velocidad de manejo urbano corto |
| `DELTA_T_MIN` | 0.5 min | Tiempo mínimo entre PODs consecutivos |
| `DELTA_T_MAX` | 90 min | Tiempo máximo entre PODs consecutivos |
| `VEL_MAX` | 60 km/h | Filtro de velocidad implícita |
| `T_SERV_MIN` | 0.1 min | Valor mínimo de t_serv para conservar el par |
| `MIN_OBS_D` | 30 | Mínimo de pares válidos por celda para reportar |

---

## Prerrequisitos del método

Aplicados en ambos scripts:

- **Orden real de visita**: pares ordenados por `Fecha_Hora_POD` (timestamp real), no por `Parada_Codigo` (orden planeado).
- **Exclusión de transportes batch**: si más del 50% de los pares de un transporte tienen `delta_t < 0.5 min`, el transporte completo se descarta.
- **Exclusión de la última parada**: la última parada de cada transporte se excluye (representa el regreso al depósito).
- **Deduplicación por stop**: múltiples ítems (`Equipo_Pieza`) en la misma parada (`Parada_Codigo`) se colapsan en un solo stop. Esto es especialmente importante para Automotriz, donde todos los ítems se scanean al mismo segundo.

---

## Salida

Cada script genera dos archivos:

- `metodo_d_resultados_*.csv` — tabla con columnas: `Sucursal`, `Segmento`, `N_pares`, `Status`, `P50_min`, `P75_min`, `CV`, `T_rec_min`
- `metodo_d_resumen_*.txt` — tabla legible en texto plano

**Regla de T rec:** `P75` cuando `CV > 0.8`, `P50` cuando `CV ≤ 0.8`.

---

## Resultados (dataset 2025 / 2026)

### Paquetería

| Sucursal | N pares | P50 | P75 | CV | T rec | Confianza |
|---|---|---|---|---|---|---|
| Córdoba | 492,931 | 2.42 | 4.08 | 0.99 | **4.08** | Alta |
| Sarandí | 417,107 | 2.37 | 3.91 | 0.94 | **3.91** | Alta |
| Rosario | 412,837 | 2.29 | 3.89 | 1.04 | **3.89** | Alta |

### Postal

| Sucursal | N pares | P50 | P75 | CV | T rec | Confianza |
|---|---|---|---|---|---|---|
| Rosario | 52,200 | 2.89 | 5.08 | 1.00 | **5.08** | Alta |
| Córdoba | 44,417 | 3.01 | 5.42 | 1.05 | **5.42** | Alta |

### Salud

| Sucursal | N pares | P50 | P75 | CV | T rec | Confianza |
|---|---|---|---|---|---|---|
| Iriarte | 5,453 | 0.95 | 4.96 | 1.94 | **4.96** | Media |
| WH Salta Salud | 5,378 | 1.05 | 2.71 | 2.04 | **2.71** | Media |

### Automotriz

| Sucursal | N pares | P50 | P75 | CV | T rec | Confianza |
|---|---|---|---|---|---|---|
| Rosario | 112 | 1.70 | 4.36 | 1.39 | **4.36** | Media |
| Sarandí | 82 | 1.08 | 4.03 | 1.81 | **4.03** | Media |
| Córdoba | 69 | 1.00 | 4.79 | 2.40 | **4.79** | Media |
| Plaza Logistica | 268 | 0.91 | 2.37 | 2.45 | **2.37** | Media |
| CBN I | 66 | 0.78 | 1.22 | 3.06 | **1.22** | Baja |
| **Combinado** | **597** | **0.97** | **3.06** | **2.32** | **3.06** | Media |

---

## Notas

- **Automotriz — scans batch**: los POD de Automotriz se registran en lote (todos los ítems de una parada al mismo timestamp). El script deduplica por `(Transporte, Parada_Codigo)` para operar sobre stops físicos, no sobre ítems individuales.
- **Método D no aplica a segmentos interurbanos**: el estimador subestima t_serv cuando la velocidad real supera los 10 km/h. Los resultados de CBN I y Plaza Logistica para Automotriz pueden reflejar perfiles operativos distintos a los de una sucursal de distribución urbana.
- **Salud — alta dispersión intrínseca**: el CV > 1.9 en Iriarte y WH Salta Salud refleja la heterogeneidad del segmento (medicamentos ambulatorios, biológicos congelados, kits). Usar con cautela.
