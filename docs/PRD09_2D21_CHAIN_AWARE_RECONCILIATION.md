# PRD 09.2D.2.1 — Reconstrucción por cadena

Estado: **BLOCKED_BY_CHAIN_MAPPING**. Implementación y certificación local
PASS; clasificación productiva incompleta. Este resultado NO autoriza cargar
históricos ni conectar Supabase. No se eligieron cantidades por conveniencia.

Base main: `811e292f9b2df3f193d71a7a3907f1800407cfc8`.
CI base: [PASS, ejecución 36216094830](https://github.com/towell2026ia/Towell_Forecast/actions/runs/36216094830).

## Fuentes verificadas, sin modificación

| Libro | SHA-256 |
| --- | --- |
| CADENAS FINAL 2023 (B) | `80d208169c3c6f5ea93cf4b03517e209f4021cc27a4a5c6be400dc0cedd106ab` |
| WM diciembre 2024 A | `989d2764ed53f29e1b2009e500dc460a19c2fb3c67057f2a390993f11a924b5e` |
| WM diciembre 2024 B | `c72fa3b0e6498b8bad248628517276a9b8c945f8160f05fd47384a18dbd7cc5d` |
| WM febrero 2025 | `1554f49647e504a0acf0a83807413eed51f50d040047b8dd577cbbe770c2e0da` |
| Todas las cadenas julio 2026, ambas copias | `fc11daae57969e293d305c5df673368463e3aea2b69e264c9832a17d07c001fb` |

Seis archivos físicos, cinco contenidos únicos. A/B 2024 son diferentes;
no se deduplican por nombre. Las dos copias 2026 sí se deduplican por bytes.
Lectura `data_only=False`, sin recalcular, guardar ni refrescar enlaces externos.
Los avisos de openpyxl sobre extensiones no producen cambios: los hashes se
comprueban otra vez al terminar. La evidencia y resultados 09.2D.2 siguen intactos.

## Inventario completo

26 pestañas 2023; 10 en cada libro 2024; siete en febrero 2025; 13 en cada
copia 2026. Total físico **79/79**; contenido único **66/66**. UNKNOWN se conserva
con revisión obligatoria si aparece en una nueva ingesta; en este corpus los
roles de las 66 hojas se pudieron clasificar. Clasificar un rol NO demuestra una
cadena ni elegibilidad de sus valores.

| Rol | Pestañas físicas | Pestañas únicas |
| --- | ---: | ---: |
| CHAIN_DETAIL | 8 | 8 |
| CHAIN_CALC | 22 | 22 |
| RAW_BASE | 15 | 14 |
| SUMMARY | 8 | 5 |
| FORECAST | 1 | 1 |
| AUXILIARY | 19 | 10 |
| ALTERNATE_REPRESENTATION | 6 | 6 |
| UNKNOWN | 0 | 0 |
| Total | 79 | 66 |

2023: BASE CM, BASE PRI, BASE CH, BASE SORM, BASE SB, BASE SORH, BASE SC,
BASE BD, BD OCT, ANCHOS, PRON, RESUMEN, BD, PRI, SC, HIPER, MERCADO, SB,
BATAS LV-WM, CH, CM, HEB, CL, Batas, DSW, PARISINA.

2024 A/B: BD, SC, BD (2), SC (2), BATAS WM, Prichos, Batas Bebés, CE,
BD WM 2024, Base. 2025: BD (2), SC (2), BATAS WM, Prichos, Batas Bebés,
CE, Base.

2026: Estadistica, BAASE, TD's, Detalle, Nuevos Julio, Nuevos Junio,
Nuevos Mayo, Nuevos Abr, Nuevos Mar, Nuevos Feb_26, Nuevos Ene_26,
Nuevos Dic, Nuevos Nov.

34 hojas únicas requieren revisión de pertenencia/grano o identidad/valor.
Ninguna se omite silenciosamente. RESUMEN aporta 82 líneas comerciales:
las líneas sin código se conservan como mapping comercial, no como producto.
No es catálogo exhaustivo: CM, HEB y PARISINA se analizan independientemente.
Los bloques de modelos sin ITEM/UPC no se expanden por descripción. En varias
bases del libro llamado 2023 los encabezados reales son 2015/2016; no se
reinterpretan como 2023. Fechas y bloques se respetan por fila/columna.

## Método y contratos

`services/assistant_api/chain_ingestion.py` es administrativo y opt-in; no se
importa ni ejecuta desde `create_app()`. `SourceSheetProfile` contiene los campos
de rol, identidad, pertenencia, valor, periodos, columnas, evidencia, padre y
formato. La configuración revisada permanece privada y ligada al hash fuente.
No existe una lista fija ni ramas por retailer/producto en el scanner.

1. Validar bytes, inventariar todas las hojas y cargar sus perfiles privados.
2. Extraer ITEM/UPC literales, incluso en hojas con métricas calculadas.
3. Resolver unidad comercial antes de formar la identidad o reconciliar valores.
4. Priorizar Cadena/Formato explícitos de la fila; no sustituirlos por el nombre
   de la hoja. Una dimensión calculada o ausente no permite fallback arbitrario.
5. Resolver una base raw únicamente con pertenencia inequívoca del mismo
   contenido fuente o mapping documentado. Un segmento pendiente también impide
   declarar artificialmente única la pertenencia de un raw fact.
6. Reconciliar `unidad + producto estable + mes + métrica`. UPC explícito se
   preserva; ITEM ambiguo entre UPCs necesita bridge. Descripción nunca es PK.
7. Deduplicar cantidades iguales conservando locators; cantidades diferentes
   siguen bloqueadas. No se suman las representaciones ni se designa ganador
   por tener UPC, sufijo, posición o nombre.
8. Trazar cada blocker anterior por locator y periodo/métrica. Si falta una
   fuente literal necesaria, no se declara resuelto por desaparición.

BD/BD (2) y SC/SC (2) tienen relaciones explícitas en perfiles revisados, no una
regla general de quitar `(2)`. Se conservan padre y formato comercial donde la
evidencia lo respalda. CE, BATAS WM y Batas Bebés aportan identidad de segmento,
pero no una partición de cadena/formato inequívoca. El maestro distingue Tipo
Compra de Formato: no se convirtió CE automáticamente en una cadena adicional.
BATAS LV-WM combina agrupaciones; no se le inventó un padre único.

Identidad/pertenencia estructural es distinta de valor. Fórmulas convencionales
y ArrayFormula se excluyen como demanda. SUMMARY, FORECAST y AUXILIARY no crean
hechos; los perfiles de detalle aceptan exclusivamente las bandas explícitas
Venta/Pedido/Entrega. Existencia, fill rate, importes y otros campos no heredan
el significado de la última banda de demanda. SUMMARY-only no asciende a A.
No hay evidencia de snapshots comparables B ni reconstrucción temporal E1/E2/E3.

## Catálogo y muestra literal

20 unidades observadas, de las cuales 17 tienen hechos elegibles. Las otras
incluyen códigos comerciales observados o catálogo sin hechos; no son cadenas
productivas ya aprobadas. Hay cinco formatos diferenciados de su padre:
BD, SC, Prichos, Hiper y Mercado. Los demás formatos explícitos se conservan
sin duplicar un padre cuyo nombre es igual al formato.

Catálogo observado: Al Super, CM, Casa Ley, Chedraui, City Fresko, DSW, GS Sears,
HEB, Liverpool, Merco, PARISINA, Sears, Soriana/Hiper, Soriana/Mercado, Suburbia,
TresB, Walmart, Walmart/BD, Walmart/Prichos, Walmart/SC. GS Sears y Sears no se
fusionan sin evidencia. CM y PARISINA conservan el código observado; no se
infiere una expansión del nombre ni una jerarquía.

13,389 filas de pertenencia: 11,720 con ITEM, 13,083 con UPC. Hay 1,444 links
ITEM y 1,512 links UPC distintos por hash/unidad; los segmentos pendientes se
incluyen en el ledger pero no son autoridad de asignación. No son conteos de
productos productivos. La certificación compara los **32,579 hechos**
seleccionados contra sus celdas literales: cero diferencias, clave comercial y
lineage correctos, 17 cadenas/formats muestreados dinámicamente.

## Reevaluación exacta de 594 blockers

Baseline congelado:
`7f5db050b05dcaa2de765faa81c3e4b16f48a4ab3672028f019bcd5d51b34fda`.
594/594 decisiones anteriores tienen ID, cluster anterior, clave anterior,
nueva clave/clasificación/resolución y estado bloqueado, con todos los locators.

| Resultado de decisiones anteriores | Cantidad |
| --- | ---: |
| RESOLVED_WRONG_CHAIN_GRAIN | 0 |
| RESOLVED_DUPLICATE_REPRESENTATION | 0 |
| RESOLVED_MEMBERSHIP | 0 |
| RESOLVED_DETAIL_VS_DERIVED | 0 |
| STILL_SOURCE_CONFLICT | 92 |
| STILL_INTERNAL_CONFLICT | 295 |
| AMBIGUOUS_CHAIN_MEMBERSHIP | 207 |
| Total explicado | 594 |

No se obtuvo evidencia suficiente para declarar resuelto ninguno de los 594.
La nueva ingesta también incorpora y registra hojas previamente no elegibles y
elimina asignaciones raw por retailer genérico: el universo de blockers cambia.
Por eso **12,350 bloqueos del corpus nuevo NO equivale a 12,350 conflictos de
cantidad ni a comparar directamente 594 → 12,350**.

| Bloqueos de todo el corpus reconstruido | Cantidad |
| --- | ---: |
| Pertenencia ambigua | 7,628 |
| Pertenencia ausente | 4,222 |
| Conflicto interno de cantidades | 408 |
| Conflicto entre fuentes | 91 |
| Conflicto de mismo corte | 1 |
| Total | 12,350 |

500 claves tienen conflictos de cantidad; 11,850 necesitan pertenencia. Los
207 anteriores están incluidos en ese segundo grupo, no se suman otra vez.
Los clusters nuevos contabilizan exactamente las claves: 110 clusters y 43
decisiones agrupadas de revisión. No se cargan datos para sortear este gate.

## Preview canónico, NO publicable todavía

| Métrica | Resultado |
| --- | ---: |
| Cadenas / productos / categorías | 17 / 797 / 50 |
| SALES / ORDER / DELIVERY | 10,823 / 10,896 / 10,860 |
| Observaciones / versiones | 32,579 / 32,579 |
| Rango | 2023-01 a 2026-07 |
| UNKNOWN / E1 / E2 / E3 | 32,579 / 0 / 0 / 0 |
| available_at fabricado | 0 |
| Fechas UNKNOWN | todas NULL |
| Missing / fórmula / inválido excluidos | 12,314 / 6,338 / 205 |
| Pre-launch / unproven zero excluidos | 7,593 / 438 |

SHA del candidato:
`fe0ca08ca4aed22b2b10778a412ce16a17df8ed58fb2df9b08bc12d9ea0b0ec7`.
Menos hechos seleccionados no significa borrar el histórico anterior: se
mantiene congelado y las fuentes descartadas/bloqueadas conservan lineage.
FENDI es sólo fixture de regresión: **96/96 PASS**, cero diferencias y misma
cobertura. No existe tratamiento especial en el código de ingesta.

## Artefactos y repetición

Todos los artefactos viven bajo `outputs/prd09_2d21/`, ignorado por Git:
sheet_inventory.json, sheet_profiles.json, chain_catalog.json,
chain_membership_ledger.json, old_to_new_conflict_map.json, reconciliation.json,
conflict_clusters.json y manual_review_pack.json. Además hay mapping RESUMEN,
determinismo, certificado de literales y muestras de regresión privados.
La ejecución preliminar queda conservada en un subdirectorio independiente.
No se publican archivos fuente, IDs operativos, cantidades por fila ni mappings.

`scripts/inspect_chain_sheets.py` inspecciona sin modificar. La reconstrucción
se ejecuta con `scripts/reconstruct_chain_history.py --config <private.json>
--old-report <frozen.json> --old-clusters <clusters.json> --output-dir
outputs/<fresh-run>`. El config privado contiene archivos, hashes revisados,
scopes/cutoffs, reglas de hojas y fingerprints del baseline congelado. Una
repetición requiere salida nueva; no sobrescribe evidencia anterior.

`scripts/certify_chain_history.py --directory outputs/<fresh-run> --config
<private.json>` comprueba cantidades y claves directamente contra XLSX fuente.
Reproducción completa en ambos órdenes: mismas versiones, seleccionados,
pertenencias, asignaciones, conflictos normalizados, conteos y SHA.

## Pruebas y límites

CP01–08: bytes y conteos de hojas reales. CP09–23: inventario, pares explícitos,
pertenencia literal/fórmula, roles, prioridad de columnas y no fanout.
CP24–30: RESUMEN completo/no exhaustivo y cadenas dinámicas sin ramas del piloto.
CP31–34: trazabilidad de 594, clusters exactos, orden independiente y copias por
hash. CP35–38: missing/prelaunch/fórmula/UNKNOWN no se convierten en cero o fecha.
CP39–40: golden 96 y certificado completo/muestras de 17 unidades.
CP41: suite existente de import mensual CSV/XLSX, preview, SHA, versionado y
SYSTEM_INGESTION. CP42: baseline SQL, seguridad 21/21 y RLS 28/28 en motor
PostgreSQL-compatible local. CP43: providers/flags y runtime sin cambios.

41 nuevos casos, incluidos cinco de corpus privado sólo local. Total local:
221 backend descubiertos, 215 PASS y seis casos de bootstrap remoto previos
NO EJECUTADOS mientras el gate está bloqueado. 110 casos de motores PASS:
**325/325 Python ejecutados PASS**. Cobertura backend **85.46%**, mínimo 81%;
chain_ingestion 97.59%. Auditoría forecast y checks estáticos PASS. Frontend
lint/typecheck/build PASS, sin cambios visuales. Lint local sólo advierte por
JavaScript de coverage en .venv; build mantiene avisos de tamaño de chunks.

CI no recibe el corpus privado: sus cinco casos se saltan explícitamente, no
se presentan como validación de fuentes públicas. CI ejecuta los tests sintéticos,
motores, auditoría, SQL/RLS, frontend, Docker build y recreación de volumen.
Docker local no está disponible; su resultado final se verifica en Linux CI.
SHA del commit entregado y enlace/estado de su CI se reportan al finalizar.

Migrations 001–007, frontend, modelos, Champion, vintages, snapshots y datos
congelados no cambian. **Supabase writes: 0**. Runtime sigue SQLite/normalized,
SUPABASE_ENABLED=false; OpenAI, Deep Research y Voice permanecen apagados.
No se modifica Railway ni se integra Netlify. RLS no se relaja.

Para avanzar se necesita evidencia de pertenencia/grano para los segmentos y
bases pendientes, y de identidad atómica para los bloques agregados antiguos;
después, autoridad de valores para conflictos reales. No basta aprobar un
nombre de hoja ni la fecha del archivo. No se fabrican relaciones, cantidades
ni available_at para obtener un gate verde.
