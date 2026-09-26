# PRD 09.2D.2.2 — Autoridad de pertenencia comercial

Estado de publicación: **BLOCKED_BY_MEMBERSHIP_AUTHORITY**. Implementación,
reconstrucción reproducible y regresión local PASS. Resolver pertenencia no
autoriza seleccionar cantidades contradictorias ni cargar Supabase.

Base: `f0f420dd43282d1c7b740e225207cf41a872d796`, main, CI base
[PASS](https://github.com/towell2026ia/Towell_Forecast/actions/runs/36219500137).

## Autoridad y alcance

La confirmación del propietario se aplica exclusivamente a hojas revisadas como
BUSINESS_COMMERCIAL. Los ITEM/UPC literales enumerados en ellas acreditan
participación en una unidad comercial, incluso si las métricas son fórmulas.
La fórmula no se recalcula ni asciende a hecho literal.

La evidencia administrativa permanece en configuración privada ignorada. Se
validan el SHA del documento del propietario, el SHA de la configuración y cada
scope hash/hoja/rol/unidad/aprobación/locator. No existe endpoint, regla generada
por navegador ni wildcard que apruebe todas las pestañas. RAW_BASE, SUMMARY,
FORECAST, AUXILIARY y bloques sin identificadores no certifican por sí solos
pertenencia de productos. Un bloque comercial sin ITEM/UPC puede acreditar la
existencia de la unidad, no identidad por descripción.

Se revisaron las 66 hojas únicas (79 físicas, seis archivos, cinco hashes).
36 hojas comerciales fueron certificadas por estructura de IDs o bloques:
14 en el libro 2023, ocho en cada contenido distinto 2024 y seis en 2025.
El maestro 2026 conserva prioridad de Cadena/Formato explícitos por fila.

Las parejas BD/BD (2) y SC/SC (2) se configuran por hash, con ambas hojas aprobadas:
cuatro relaciones en los dos libros 2024. No se elimina un sufijo por heurística.
En 2025 las representaciones comerciales existentes se revisan explícitamente;
no se inventa una pareja ausente. UPC puede reforzar identidad, nunca autoridad
sobre el valor. Los códigos independientes conservan padre desconocido; no se
adivinan retailers ni expansiones de nombres. No hay enum fijo de unidades.

## Reconstrucción y seguridad temporal

La identidad es unidad comercial + producto estable + mes + métrica.
La pertenencia se filtra por hash fuente, periodo observado e ITEM/UPC.
Cadena/Formato explícitos ganan; raw sin discriminador se asigna sólo cuando
la pertenencia es única. Si hay múltiples o ninguna, bloquea sin fan-out.
ITEM se une a UPC sólo cuando hay un bridge único en unidad/periodo. No se
importa un UPC de otro periodo para resolver artificialmente la identidad.

Cada registro conserva el conjunto exacto de periodos observados y sus extremos,
no un catálogo permanente ni relleno de huecos. Ausencia fuera del alcance no
demuestra no-pertenencia. No se usan nombres de archivos, mtimes ni la fecha de
aprobación como `available_at`. Todos los históricos siguen UNKNOWN/NULL.

Las antiguas sugerencias de unidad de perfiles raw no se heredan como autoridad
nueva: los mappings de soporte necesitan alcance documental ratificado, o
pertenencia atómica observada. Esto no borra el mapping, evidencia ni preview
anterior, que permanecen congelados. En particular, los bloques 2023 sin IDs
no permiten reconstruir un producto a partir de su nombre; varios encabezados
de bases antiguas contienen realmente 2015/2016, no 2023. El nuevo preview
seleccionado empieza en 2024, sin inventar observaciones 2023.

## Contabilidad del baseline exacto

| Pertenencia anterior: 11,850 claves | Resultado |
| --- | ---: |
| Resueltas por hoja comercial | 1,244 |
| Resueltas por fila explícita | 0 |
| Resueltas por ITEM/UPC único | 5,214 |
| Múltiples unidades pendientes | 162 |
| Sin pertenencia demostrable en alcance | 5,230 |
| Identidad insuficiente | 0 |
| Total | 11,850 |

De 7,628 ambiguas + 4,222 faltantes se resuelven **6,458** (54.50%) y
permanecen **5,392**. Cada clave anterior retiene status, candidatos, pruebas,
regla, nuevos keys y locators; no se declara resolución por desaparición.

Los **500** conflictos de valor anteriores conservan íntegros sus locators y
cantidades: 408 internos, 91 entre fuentes, uno del mismo corte. Separados por
unidad: **0**. No se aprobó un ganador de valores. Una separación sólo se permite
si las fuentes prueban unidades diferentes y todos los keys resultantes tienen
hechos aceptados, sin contradicciones pendientes.

## Universo completo: no confundirlo con la cohorte anterior

El nuevo análisis completo tiene **9,016** claves pendientes de pertenencia:
162 múltiples y 8,854 sin pertenencia. Son **3,624** adicionales a las 5,392
pendientes de la cohorte, al dejar de heredar mappings raw sin nueva autoridad.
También hay **729** conflictos de valores: 637 internos, 91 entre fuentes y uno
del mismo corte. Incluyen los 500 originales y **229 nuevos** conflictos internos
expuestos por la reconstrucción. No se ocultan ni resuelven por conveniencia.
Total de bloqueos del nuevo universo: **9,745**.

La revisión privada agrupa la pertenencia en **12 decisiones** por motivo y
hash/hojas, no miles de solicitudes. Pide discriminadores de detalle para raw
multiunidad y mappings/catálogos con alcance observado para las bases sin
pertenencia demostrable. Una respuesta sólo podrá aplicarse a su scope aprobado.
Resolver la pertenencia no resuelve automáticamente los conflictos de cantidades.

## Preview administrativo

| Medida | Resultado |
| --- | ---: |
| Catálogo total | 25 unidades |
| CERTIFIED / OBSERVED / REVIEW_REQUIRED | 24 / 1 / 0 |
| Unidades con hechos elegibles | 20 (antes 17) |
| Productos | 874 |
| Categorías | 50 |
| SALES / ORDER / DELIVERY | 10,749 / 10,937 / 10,938 |
| Observaciones seleccionadas | 32,624 |
| Periodo | 2024-01 a 2026-07 |
| UNKNOWN / available_at fabricados | 32,624 / 0 |

SHA del dataset:
`ed8f7514d56e7c0684856131ee9610252507259a2367769af5310e306a16dc04`.
El catálogo certificado incluye unidades sin actuals atómicos elegibles;
certificar la unidad no certifica todos sus productos ni autoriza publicación.

## Regresión y verificación

FENDI en Walmart::BD: **96/96**, cero diferencias respecto del golden congelado.
Sin ramas de producción específicas del piloto o retailer. Se verificaron las
32,624 cantidades seleccionadas contra celdas literales originales, con muestra
y lineage por cada una de las 20 unidades elegibles. Hashes de los seis archivos
sin cambios; orden invertido de fuentes, perfiles y scopes produce exactamente
el mismo dataset, pertenencias, assignments y conflictos. Baseline y evidencia
anteriores intactos.

Los tests públicos usan datos sintéticos. La certificación real se ejecuta sólo
localmente si existen los artefactos privados; CI no descarga ni publica el corpus.
Se cubren CP01–36, suite backend, motores, audit genérico, schema/RLS locales y
frontend lint/typecheck/build. El Docker build y el reinicio con volumen se
verifican mediante el CI existente; no hay Docker disponible en este host.

## Reproducción privada

`scripts/reconstruct_business_membership.py` requiere `--baseline-dir`,
`--baseline-dataset-sha`, `--baseline-versions-sha`, `--authority`,
`--authority-sha`, `--rule`, `--rule-sha` y `--output-dir`.
Rechaza modificaciones al baseline 11,850/500 y archivos fuente cambiados.
Escribe únicamente en un directorio nuevo de `outputs/`, sin sobrescribir
resultados congelados. Los paths reales, configuración, valores, IDs de productos,
traces, muestras y review pack se conservan sólo en `outputs/prd09_2d22/`, ignorado
por Git. Una copia de la primera verificación se conserva privadamente.

Artefactos: evidencia, catálogo, aliases, ledger de pertenencia, resolución de las
11,850 claves, rekey de los 500 conflictos, reconciliación, clusters, revisión
agrupada y RESULT. Los registros son pre-ingestión administrativa, no publicación.

Supabase writes **0**. No llamadas remotas ni cargas de metadatos/datos/archivos.
Migraciones 001–007, RLS, frontend, modelos, runtime, snapshots y vintages
congelados no cambian. Sigue SQLite/normalized, SUPABASE_ENABLED=false;
OpenAI, Deep Research, voz y Netlify no se conectan. Sin modificación de Railway.
