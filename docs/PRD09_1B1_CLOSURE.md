# PRD 09.1B.1 — Desacople y disponibilidad temporal

## Núcleo activo y legado

`create_app()` instancia por defecto `GeneralizedMonthlyForecastRunner` y
`GeneralizedHistoricalForecastRunner`. El input activo proviene de observaciones
normalizadas persistidas, no del CSV del piloto. Los snapshots del dashboard
heredado siguen disponibles sólo para lectura de la interfaz existente; no son
insumo del motor nuevo. `HistoricalForecastRunner` se conserva bajo el alias
`LegacyHistoricalForecastRunner` para reproducir evidencia congelada. Sólo se
instancia al configurar explícitamente `LEGACY_PILOT_ENABLED=true` (valor por
defecto: `false`). El pipeline mensual anterior sólo puede seleccionarse con
ese flag y una inyección explícita del pipeline. Ninguna ejecución del legado
se activa automáticamente.

## Disponibilidad temporal

`ImportService.ingest` registra un `ImportBatch` inmutable con `batch_id`,
`source_name`, `filename`, SHA-256, `period`, `uploaded_at`, `uploaded_by`,
`availability_source`, `evidence_id` y `status`. La API pública no expone aún
una ruta de carga: el servicio es un contrato interno hasta disponer de
autenticación y normalización de archivos aprobadas. El reloj confiable del
servidor define `uploaded_at` y el `available_at` de cada observación del lote;
el llamador no puede aportar ese instante. Una fecha presente en el archivo es
sobrescrita. El lote y sus filas se guardan en tablas separadas de la evidencia
histórica y de los vintages congelados.

La observación normalizada admite identidad de cadena/producto, atributos del
producto, objetivo, periodo, valor, ausencia, `available_at`,
`availability_source`, `availability_evidence_id` e `import_batch_id`.
`period` indica cuándo ocurrió el movimiento; `available_at`, cuándo podía ser
conocido. El replay limita `period <= cutoff` y `available_at <= cierre del
cutoff`; un lote recibido después de ese cierre se rechaza para esa corrida.

El CSV heredado sin disponibilidad verificable permanece `UNKNOWN` y la corrida
correspondiente queda `BLOCKED_AVAILABILITY`. Ni fin de mes ni mtime del archivo
se usan como sustitutos. Para evidencia histórica, el resolver requiere una
asignación validada por el auditor anterior **y** un manifiesto registrado
E1/E2/E3 con hash de fuente y fecha coincidentes. E3 también exige celdas
verificadas y revisión documentada. La salida identifica
`availability_source=LEGACY_EVIDENCE` y el `evidence_id`; no altera la fila
original ni el manifiesto. E4/E5/E6/UNKNOWN no desbloquean historia.

## Seguridad y certificación

`POST /api/forecast/run` acepta `period`, `chain_id` opcional y `objective`;
requiere permiso de ejecución y permanece deshabilitado cuando
`AI_ASSISTANT_API_ENABLED=false`, como en Railway bootstrap.
`POST /api/historical/run` acepta cadena, rango y objetivo, crea un job
idempotente para el runner genérico y también permanece deshabilitado bajo ese
flag. La ruta heredada `first-vintage` requiere además `LEGACY_PILOT_ENABLED`.
El audit sintético comprueba motor, Champion, horizontes, aislamiento del legado
y disponibilidad.
**Audit PASS no equivale a certificación productiva**: esta última requiere
datos reales con disponibilidad y ventanas suficientes. No se activó Supabase,
OpenAI, Deep Research, voz, ni publicación automática de Champion.

## Evidencia y despliegue

No se modifican snapshots, hashes, vintage E3, manifiestos ni las 346 celdas
verificadas. Gate 0 Railway fue validado para el commit base
`574735f7630babd0fc2efdb9b14c73cb518c3bf1`: `/api/live` y `/api/health`
respondieron 200. Después del push se deben repetir esas consultas y comprobar
que `/api/version` devuelva el SHA nuevo. CI ejecuta la auditoría, pruebas,
cobertura, frontend y smoke Docker. Hasta que Railway reporte el nuevo SHA,
el cierre de despliegue no debe declararse PASS.
