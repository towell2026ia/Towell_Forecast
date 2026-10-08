# PRD 09.2E4 — cálculo, decisión, captura y aprendizaje LIVE

Base: `ea05005bb0aac98ff891f522e0e6f2c5399bfaa9`. Objetivo operacional: Venta; 12 horizontes por cálculo. Esta entrega prepara código y la migración `202610080012_e4_calculation_history.sql`. **No aplica SQL al proyecto remoto ni activa producción.** Los tests locales usan datos sintéticos en PGlite y Python, no son aceptación productiva de FENDI AZUL.

## Objetos y estados

- `forecast_calculations`: C1, C2, C3 secuenciales por `(chain_id, product_id, objective)`, con hash de datos, IDs de observaciones de entrada, snapshot de producto/candidatos/investigación, referencia sugerida, motor, SHA y motivo de recálculo. Su estado puede pasar de `READY_FOR_DECISION` a `DECIDED` y después `SUPERSEDED`; los datos del cálculo no cambian. El contrato admite además `QUEUED`, `RUNNING`, `FAILED` y `BLOCKED`, aunque el RPC de esta fase sólo confirma cálculos completos de forma atómica.
- `forecast_calculation_horizons`: 12 filas exactas por cálculo; estadístico, ML y Ensemble conservan valores independientes **a nivel producto**. El ML del scope no sustituye al ganador ML del producto. Cuando el Ensemble usa otro ML del scope, congela su propio componente, modelo y pesos; un `CHECK` valida la combinación por horizonte. Candidatos no elegibles quedan `NULL`. Con evidencia insuficiente, P10/P90/P95 quedan `NULL`; P50 conserva literalmente la curva central provisional del preview, sin certificación probabilística, con `INSUFFICIENT` y observaciones visibles. Las bandas disponibles congelan la curva provisional original, no se reinterpretan automáticamente para cada decisión posterior.
- `forecast_selection_events`: eventos append-only. Cada selección congela una curva completa H1–H12, el actor, motivo y vínculo con selección previa. `current_forecast_selection` devuelve la última decisión del producto/scope. Seleccionar C2 no recalcula C1 y crear C2 sin decidirlo deja C1 vigente.
- `forecast_capture_sessions`: borrador y confirmación. La confirmación crea un `import_batch` de plataforma y tres filas en `monthly_observations` (`ORDER`, `SALES`, `DELIVERY`) con siguiente `version_no`. `available_at` coincide exactamente con `uploaded_at` real del batch confirmado. Corregir requiere un motivo y añade V2, sin actualizar V1.
- `forecast_live_evaluations`: cada cálculo maduro se evalúa en su horizonte original contra la misma versión de Venta. Hay filas independientes para Estadístico, ML, Ensemble y `TOWELL_SELECTED` cuando la decisión precede a la **primera** disponibilidad de la Venta. Una decisión posterior al real no se presenta como acierto LIVE, aun si se corrige la Venta. La clave única incluye la versión real mediante `sale_observation_id`; las correcciones conservan evaluaciones anteriores. Pedido y Entrega se congelan con IDs y valores para calcular Fill Rate sin perder linaje.
- `forecast_learning_events`: señales y regret/value-added estructurados, append-only. No entrena, no cambia pesos, no publica vintage y no promueve Champion. `forecast_live_closures` aplica idempotencia al cierre.

`forecast_vintages`, `forecast_horizons`, `champion_registry` y las tablas históricas existentes permanecen separadas e intactas. Un cálculo C1 no es un vintage, una referencia sugerida ni un Champion.

## APIs y autenticación

Todos los endpoints exigen JWT Supabase verificado en FastAPI. Las lecturas viajan a Supabase con el JWT del usuario y RLS. Las escrituras usan exclusivamente RPC transaccionales del backend con credencial de servicio guardada en Railway; esa credencial nunca llega al navegador. Cada RPC revalida que el actor esté activo y autorizado para la cadena. Los endpoints de escritura exigen `Idempotency-Key`:

| Endpoint | Flag | Permiso |
| --- | --- | --- |
| `POST /api/forecast/calculations` | cálculo | ADMIN o EDITOR con `can_run_forecast` |
| `GET /api/forecast/calculations` y `/{id}` | cálculo | lectura de cadena |
| `POST /api/forecast/calculations/{id}/select` | selección | ADMIN; motivo si difiere de sugerencia |
| `GET /api/forecast/current-selection` | selección | lectura de cadena |
| `POST /api/forecast/observations/capture` y `/{session}/confirm` | captura | ADMIN o EDITOR con `can_edit` |
| `GET /api/forecast/observations/capture` | captura | lectura de cadena |
| `POST /api/forecast/live-evaluation` | LIVE | ADMIN |
| `GET /api/forecast/live-metrics` y `/learning` | LIVE | lectura de cadena |

El botón de cálculo en Decisión inicia o reutiliza el preview matemático, espera H1–H12 completos y los congela mediante el RPC. Si cambió el hash de datos del preview, FastAPI bloquea la escritura; no reinterpreta automáticamente entradas históricas. Deep Research sólo se guarda como snapshot de contexto y nunca entra a la selección o las fórmulas.

En Historial, la comparación toma por defecto la decisión vigente y el cálculo reemplazado más reciente. La gráfica y la tabla usan las **curvas seleccionadas** alineadas por periodo, no dos curvas estadísticas por conveniencia. Venta real sólo aparece si existe una evaluación LIVE confirmada; ausencia de decisión o de real se muestra como vacío, no como cero.

## Métricas y evidencia

- Error individual: `absolute_error = abs(Venta - forecast)`, `signed_error = Venta - forecast`; APE sólo si Venta es mayor que cero.
- WAPE LIVE agregado por candidato y horizonte = `100 * sum(absolute_error) / sum(Venta)`, nunca promedio de APE. Bias = `100 * sum(signed_error) / sum(Venta)`.
- Fill Rate sobre pares válidos = `100 * sum(Entrega) / sum(Pedido)`; denominador cero produce `NULL`.
- Para métricas actuales se toma sólo la última versión de Venta por cálculo/horizonte/candidato; V1 permanece auditable. Regret de decisión es el error elegido menos el mínimo de los candidatos disponibles. Value Added de selección sólo se informa si Towell se separó de la sugerencia. Son señales observacionales, no una decisión automática de Champion.
- `RETROSPECTIVE_TRAINING` conserva su advertencia de no certificación point-in-time. No se fabrican fechas `available_at` para histórico heredado.

## Activación y reversión

Valores por defecto — también en `.env.example` — son `false`:

```text
FORECAST_CALCULATION_HISTORY_ENABLED=false
FORECAST_SELECTION_ENABLED=false
CAPTURE_CENTER_ENABLED=false
LIVE_LEARNING_ENABLED=false
```

Antes de cambiar cualquier flag: aprobar por separado y aplicar las migraciones pendientes 011/012, verificar RLS, permisos RPC, volumen, CI y SHA de Railway/Netlify. Luego activar **de uno en uno**: historial de cálculos, selección, captura, LIVE; verificar permisos y datos en cada etapa. La activación exige `OPERATIONAL_PREVIEW_ENABLED=true`, Supabase y `SUPABASE_SERVICE_ROLE_KEY` sólo en Railway. Nunca copiar service role, OpenAI key, JWT o conexión a Netlify/Git.

Rollback funcional: volver los cuatro flags a `false`; si hace falta desplegar el SHA anterior. No borrar cálculos, capturas ni evaluaciones ya creadas. No ejecutar `supabase db push` como parte de este PRD sin aprobación específica. La aceptación FENDI AZUL C1/C2, decisión humana y cierre real requiere después migración remota y datos autorizados: no puede marcarse PASS con fixtures locales.

## Comprobaciones locales

`node scripts/test_e4_schema.mjs` carga las migraciones completas en PostgreSQL aislado y prueba RLS, C1/C2, 12 horizontes, inmutabilidad, autorización RPC, borrador/confirmación, V1/V2/V3, cierre LIVE y rechazo de decisión posterior al primer real. `python -m unittest services.assistant_api.test_calculation_history` valida curvas producto/scope, ausencia de ML, motivos, horizontes originales, WAPE/Fill Rate y señales sin Champion. El flujo UI se cubre en `tests/portal/e4.test.tsx`. El CI ejecuta además todas las suites existentes, typecheck, lint, builds y Docker.
