# PRD 3A.7 — Preparación automatizada privada

El procedimiento genera una candidata reproducible desde el maestro comercial y
una instantánea de lectura de Supabase. No confirma importaciones, ejecuta SQL,
recalcula pronósticos ni activa versiones. Sólo existen `prepare` y `verify`.

## Contratos reutilizados

- `planning_pilot.engine.read_source`: lectura del Excel original sin guardarlo ni
  actualizar vínculos; se inventarían además todas sus hojas y columnas.
- `historical_corpus.number`: cantidades exactas con Decimal, vacíos y ceros.
- `inventory_preflight.automatic_reconciliation.group_key`: identidad de grupo
  por cuenta, cadena, formato, modelo, presentación y código comercial.
- `monthly_imports.ImportProfileVersion/parse_monthly`: perfiles versionados y
  validación local de CSV de Pedido; no se llama `upload` ni `confirm`.
- `supabase_access.SupabaseReadClient`: GET con sesión Auth acreditada y RLS,
  sin service role. Dos lecturas deben coincidir; el modo offline exige SHA256.

La conciliación usa UPC literal + scope comercial de la columna D que coincida
con el código de catálogo (o su denominación terminal única). No se deducen
equivalencias a partir del UPC, semejanza de descripciones o razón social. La
cadena original B y el formato D siguen visibles. Una contradicción de cadena
conocida, scope no único, producto inactivo, duplicado de destino o anomalía de
origen impide sólo esa vinculación. Los candidatos se conservan en el reporte.

## Semántica y comparación

| Hoja/columna | Variable candidata | Contrato vigente |
|---|---|---|
| Towell K/L/M, años 2024/25/26 | FCST_TOWELL_HISTORY | Pendiente de extensión |
| Cliente K/L/M, años 2024/25/26 | FCST_CLIENTE_HISTORY | Pendiente de extensión |
| Pedidos K/L/M, años 2024/25/26 | ORDER | Parser existente |
| Ausente del maestro | SALES | Conservar exactamente la base |
| Ausente del maestro | DELIVERY | Conservar exactamente la base |

Todo está en piezas por confirmación del responsable. No se usan factores de
empaque y no se suman las variables entre sí. Propuestas de resurtido y resúmenes
se inventarían como evidencia, sin tratarlos como ventas ni objetivos nuevos.
Un vacío conserva la fila vigente; un cero explícito sí es cantidad. Coincidir no
genera otra versión. Cambiar genera una propuesta append-only con versión previa
+ 1 e identificador determinista. No se escribe ninguna de esas propuestas.

La comparación incluye producto, scope, variable y mes; usa la vista
`portal_monthly_observations_current`, sin filtrar su visualización por evidencia
temporal. Un dato sin `available_at` no se acredita para WAPE LIVE. La confirmación
de cierre de octubre no inventa una fecha histórica de disponibilidad.

La candidata simula sólo Pedido elegible. Los forecasts históricos se muestran
como series privadas pendientes de contrato. Venta y Entrega de la instantánea
permanecen intactas. Por modelo se suman las variantes una sola vez y se conserva
cobertura; ninguna ausencia se rellena con cero. El CSV de importación usa el
product_code y variant_code del destino verificado, conserva su descripción, y
registra separadamente UPC/celda originales. El parser y la resolución local de
la identidad deben devolver exactamente el producto previsto.
Si el parser vigente normalizaría una descripción del catálogo, la fila cambiada
queda fuera del paquete con motivo explícito; una fila idéntica conserva su
observación anterior. No se altera metadata para hacer pasar una importación.

## Ejecución local

Archivos de entrada y salida siempre privados, fuera del repositorio anidado:

```powershell
python -B scripts/prepare_operational_update.py prepare `
  --source '<ruta privada>/maestro.xlsx' `
  --snapshot '<ruta privada>/snapshot.json' `
  --snapshot-sha256 '<sha256 acreditado>' `
  --cutoff 2026-10 --expected-identities 427 --expected-groups 151 `
  --preserve '<ruta privada>/version-anterior.json' `
  --output '<ruta privada>/nueva-version'
python -B scripts/prepare_operational_update.py verify `
  --output '<ruta privada>/nueva-version' --manifest-hash '<sello externo>'
```

Alternativa `--live-read`: configuración existente `SUPABASE_URL`,
`SUPABASE_ANON_KEY`, `FORECAST_REVIEW_ACCESS_TOKEN`. No imprimir ni guardar tokens.
La instantánea offline contiene `chains`, `products`, `profiles`, `observations`
y `protected` con las mismas estructuras de los GET existentes. El snapshot y
las huellas de base deben obtenerse con lecturas autorizadas y coherentes. Un
snapshot no sustituye un respaldo restaurable.

Se rechazan rutas dentro del repositorio y carpetas de salida existentes. La
fuente y cada `--preserve` se verifican antes y después de exportar. Cada paquete
incluye manifiesto con hashes de todos sus archivos, comparaciones, identidades,
excepciones, vistas completas por color/modelo, HTML, CSV por scope y plan de
activación. El sello externo liga también el índice: no basta alterar un CSV y
reescribir su hash. Las salidas completas nunca se incorporan a CI o GitHub.

## Propuestas mínimas pendientes, sin SQL aplicado

El contrato `202609250001_contract_core.sql` limita `monthly_observations.metric_code`
a SALES/ORDER/DELIVERY y `value` a numeric(18,3). Se propone ampliar los códigos a
las dos historias de forecast, sin copiar filas a SALES, y añadir sus columnas a
los mapeos/parser, lecturas y validaciones correspondientes. Mantener linaje de
lote y perfil, versión, RLS por cadena y restricciones existentes. Revisar cómo
exponer esas métricas sin cambiar el significado de las cinco variables actuales.
Se documenta la propuesta; no se agrega ni ejecuta migración o cambio de lector.

Las cantidades con más de tres decimales o fuera de rango quedan en excepción,
sin redondear. Resolverlas exige confirmación comercial o propuesta de precisión
separada. La extensión de códigos no resuelve por sí sola la precisión.

`202609280001_portal_published_history.sql` elige la versión mayor de cada clave
cuyo lote está IMPORTED. No hay selector de release que vuelva atómicamente al
conjunto previo. Por eso no se puede prometer una reversión completa mediante
filas compensatorias: no eliminarían lógicamente claves nuevas. Se propone
autorizar un selector mínimo que reutilice lotes/observaciones, mantenga RLS y
permita elegir manifiesto anterior/candidato. Antes de implementar ese cambio de
lectura se requiere decisión expresa; no se crea aquí una estructura productiva.

## Respaldo, activación y reversión

1. Congelar candidatas y sello; exportar privadamente hechos físicos/versiones,
   perfiles, lotes, linaje, catálogo, DDL/políticas y resultados protegidos.
   Ensayar restauración aislada y reconciliar conteos/hashes.
2. Resolver excepciones a incluir; aprobar los contratos/selector pendientes y
   probarlos aisladamente. Mantener fuera las identidades no acreditadas.
3. Releer el estado productivo y verificar huella global y versiones por clave.
   Cualquier cambio exige regenerar una candidata, sin sobrescribir la anterior.
4. Solicitar autorización explícita sobre manifiesto y alcance. Reutilizar la
   validación y confirmación transaccional existentes. No crear productos ni
   categorías, cambiar metadata, borrar hechos o inventar available_at históricos.
5. Cargar/validar aisladamente; comparar todos los productos/variables/meses,
   sumas por modelo y hashes protegidos. Activar sólo el selector autorizado.
6. Ante fallo, volver atómicamente al manifiesto anterior y conservar ambas
   versiones. Verificar mismos C1, WAPE, Champions y SALES/DELIVERY. Sin ensayo de
   respaldo/reversión, no activar. Este programa no ejecuta esos pasos.

## Pruebas

`python -B -m unittest services.operational_update.test_preparation -v` cubre
UPC con ceros iniciales, aislamiento de scopes, excepciones individuales,
duplicados, variables independientes, precisión, meses no cerrados, sumas
parciales, identidad del importador, concurrencia, conservación, reproducibilidad
y sellos contra alteraciones. Los fixtures son sintéticos, sin datos comerciales.
