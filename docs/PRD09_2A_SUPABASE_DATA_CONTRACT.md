# PRD 09.2A — Contrato de datos Supabase v1

Estado: **contrato propuesto para aprobación**. Este documento fija el modelo lógico y los invariantes que deberán traducirse a SQL en 09.2B. No es una migración ejecutable ni autoriza un cutover. El proyecto es único y multichain; Railway conserva el cálculo Python. `PersistenceProvider` y `DataProvider` siguen siendo las fronteras del motor.

## Convenciones normativas

- `id` es UUID técnico, salvo donde se indique una clave compuesta. Todos los FK son restrictivos para hechos/versiones congelados: borrar una cadena, producto o evidencia nunca borra su historia en cascada. Los identificadores legacy se conservan en un mapa de migración; no se convierten en UUID inventando semántica.
- Todo `period`, `issue_period` y `target_period` de grano mensual es `DATE` del **primer día del mes**. `TIMESTAMPTZ` se almacena con instante UTC; la zona comercial de `chains.timezone` sólo afecta presentación/cierre autorizado.
- Toda tabla que representa datos de una cadena lleva `chain_id` o llega a él mediante un FK obligatorio e inequívoco. Una referencia cruzada debe pertenecer a la misma cadena. La API nunca usa descripciones como identidad de producto.
- `available_at` significa cuándo el dato era conocible, no cuándo ocurrió. Para nueva ingesta confirmada es el `uploaded_at` sellado por el backend. Para historia sin prueba es `NULL` y `availability_source=UNKNOWN`; migrar no desbloquea ese registro. E1/E2/E3 requieren manifiesto y asignación verificados. El replay aplica `period <= target_cutoff` y `available_at <= execution_cutoff`.
- Los estados y códigos enumerados aquí son vocabularios controlados del contrato; 09.2B decidirá entre `CHECK` y tipos enum con migraciones evolutivas. Valores numéricos de demanda/pronóstico son no negativos; `BIAS` y ajustes pueden ser negativos.
- `Fcst Cliente` es benchmark externo, **no** variable objetivo de ML. `Forecast Towell` y los vintages congelados nunca se actualizan por decisiones humanas.

## Entidades, claves y relaciones

En cada entrada, los campos no marcados `?` son obligatorios. `created_at`/`updated_at` son `TIMESTAMPTZ` de servidor. Las reglas de unicidad son lógicas y deberán imponerse en PostgreSQL, incluidas las columnas anulables.

### Master data

| Entidad | Campos tipados principales | FK y unicidad |
|---|---|---|
| `chains` | `id UUID PK`, `code TEXT`, `name TEXT`, `status TEXT {ACTIVE,INACTIVE}`, `timezone TEXT`, `created_at`, `updated_at` | `code` único global, normalizado; una cadena no se crea por cada carga. |
| `categories` | `id UUID PK`, `chain_id UUID`, `code TEXT`, `name TEXT`, `status TEXT`, `created_at` | `chain_id → chains.id`; único `(chain_id,code)`. |
| `products` | `id UUID PK`, `chain_id UUID`, `category_id UUID?`, `product_code TEXT`, `variant_code TEXT?`, `description TEXT`, `variant_description TEXT?`, `status TEXT {NEW,ACTIVE,INACTIVE}`, `first_seen_period DATE`, `last_seen_period DATE`, `created_from_batch_id UUID`, `created_at`, `updated_at` | `chain_id → chains`; `category_id → categories` de la misma cadena; `created_from_batch_id → import_batches`. Llave natural única `(chain_id,product_code,COALESCE(variant_code,''))`; `NULL` y cadena vacía de variante se normalizan a `NULL`. Descripción nunca es llave. |

`product_id` no cambia si cambian descripción, categoría o variante textual. Un producto ausente de un archivo permanece en catálogo y conserva todo su historial; sólo `last_seen_period` se actualiza para productos presentes. `INACTIVE` requiere una política futura explícita. El primer hallazgo crea `NEW`; el motor determina por separado `COLD_START`/`PROVISIONAL`.

### Intake y evidencia

| Entidad | Campos tipados principales | FK y unicidad |
|---|---|---|
| `import_profiles` | `id UUID PK`, `chain_id UUID`, `name TEXT`, `status TEXT`, `created_at` | `chain_id → chains`; único `(chain_id,name)`. |
| `import_profile_versions` | `id UUID PK`, `profile_id UUID`, `version INTEGER>0`, `mapping_json JSONB`, `required_columns JSONB`, `created_at`, `created_by UUID` | `profile_id → import_profiles`; `created_by → auth.users`; único `(profile_id,version)`. Una versión usada no se edita. |
| `import_batches` | `id UUID PK`, `chain_id UUID`, `profile_version_id UUID`, `filename TEXT`, `sha256 CHAR(64)`, `storage_path TEXT?`, `period DATE`, `uploaded_at TIMESTAMPTZ`, `uploaded_by UUID`, `availability_source TEXT`, `evidence_id UUID?`, `row_count INTEGER`, `valid_rows INTEGER`, `rejected_rows INTEGER`, `status TEXT`, `created_at` | FK a cadena, versión de perfil de esa cadena, `auth.users`, `source_evidence?`. Único `(chain_id,sha256)`; reintento devuelve el batch previo, nunca crea información duplicada. Contadores no negativos y `valid_rows+rejected_rows <= row_count`. |
| `source_evidence` | `id UUID PK`, `chain_id UUID?`, `legacy_id TEXT?`, `evidence_level TEXT {E1,E2,E3,UNKNOWN}`, `evidence_type TEXT`, `evidence_date TIMESTAMPTZ?`, `source_name TEXT`, `source_hash CHAR(64)`, `storage_path TEXT?`, `description TEXT`, `validated_by UUID?`, `validated_at TIMESTAMPTZ?`, `created_at` | FK a cadena y validador cuando existan. `legacy_id` único si existe. Sin prueba temporal, `evidence_date=NULL`; E1/E2/E3 exigen validación y hash. Evidencia congelada es insert-only. |

Estados de batch: `UPLOADED → VALIDATING → VALIDATED → CONFIRMED → IMPORTED`; `REJECTED` y `FAILED` son salidas explícitas con motivo auditable. Se permite reintento idempotente controlado, no un segundo batch para el mismo `(chain_id,sha256)`. El archivo original reside en `source-files` privado. El backend sella `uploaded_at` al recibirlo; ni navegador, Excel ni mtime pueden proveerlo. Preview requiere conteos, productos nuevos/existentes/actualizados, advertencias y errores clasificados `BLOCKING/WARNING/INFO` antes de confirmar. La representación física del staging/preview se decide en 09.2D; no se escriben hechos finales durante preview.

La confirmación ocurre en **una transacción**: validar batch y perfil, auto-upsert de categorías/productos, insertar observaciones versionadas, registrar evidencia/lineage, actualizar contadores y estado, y emitir auditoría. Una falla revierte todo. El mismo hash en otra cadena es otro ámbito; el mismo hash en la misma cadena no se importa dos veces.

### Hechos operativos y Fcst Cliente

| Entidad | Campos tipados principales | FK y unicidad |
|---|---|---|
| `monthly_observations` | `id UUID PK`, `chain_id UUID`, `product_id UUID`, `period DATE`, `metric_code TEXT {SALES,ORDER,DELIVERY}`, `value NUMERIC`, `version_no INTEGER>0`, `available_at TIMESTAMPTZ?`, `availability_source TEXT`, `availability_evidence_id UUID?`, `source_batch_id UUID?`, `created_at` | Producto y batch deben pertenecer a `chain_id`; FK a evidencia/batch. Único `(chain_id,product_id,period,metric_code,version_no)`. Valor no negativo. `UNKNOWN` exige `available_at=NULL`; `SYSTEM_INGESTION` exige batch y `available_at=batch.uploaded_at`. |
| `customer_forecast_versions` | `id UUID PK`, `chain_id UUID`, `issue_period DATE`, `received_at TIMESTAMPTZ`, `version_no INTEGER>0`, `status TEXT {DRAFT,VALIDATED,FROZEN,SUPERSEDED}`, `created_by UUID`, `created_at`, `validated_by UUID?`, `validated_at TIMESTAMPTZ?`, `frozen_at TIMESTAMPTZ?` | FK a cadena y usuarios; único `(chain_id,issue_period,version_no)`. Un header congelado es inmutable; nueva cifra crea otra versión. |
| `customer_forecast_rows` | `id UUID PK`, `forecast_version_id UUID`, `product_id UUID`, `target_period DATE`, `value NUMERIC`, `created_at` | FK a versión y producto de la misma cadena; único `(forecast_version_id,product_id,target_period)`. Valor no negativo. |

Una corrección real crea `version_no+1`; nunca hace `UPDATE value` sobre V1. `monthly_observations_current` seleccionará la mayor versión **válida** por `(chain,product,period,metric)` sin borrar versiones antiguas. La consulta histórica debe además limitar `available_at` al cutoff; una vista “current” sin ese filtro no sirve para backtesting. El estado `UNKNOWN` permanece bloqueado. Un draft de Fcst Cliente puede editarse sólo con auditoría; al congelarlo no se modifica, y V2 preserva V1. No se acepta `target_period < issue_period` sin política explícita.

### Forecast, modelos y resultados

| Entidad | Campos tipados principales | FK y unicidad |
|---|---|---|
| `forecast_runs` | `id UUID PK`, `run_code TEXT`, `chain_id UUID`, `objective TEXT {Venta,Pedido,Entrega}`, `issue_period DATE`, `cutoff_at TIMESTAMPTZ`, `status TEXT`, `data_snapshot_hash CHAR(64)`, `research_snapshot_id UUID?`, `started_at TIMESTAMPTZ?`, `finished_at TIMESTAMPTZ?`, `actor_id UUID?`, `engine_version TEXT`, `git_sha TEXT`, `error_code TEXT?`, `created_at` | Cadena obligatoria; `run_code` único. Una solicitud de “todas las cadenas” crea runs separados por cadena. Estados: `QUEUED,PREPARING,RESEARCH,STATISTICAL,ML,ENSEMBLE,CERTIFICATION,SAVING,COMPLETED,FAILED`. |
| `model_versions` | `id UUID PK`, `run_id UUID`, `chain_id UUID`, `model_family TEXT {STATISTICAL,ML,ENSEMBLE}`, `algorithm TEXT`, `model_version TEXT`, `parameters_json JSONB`, rangos `training_start/end`, `validation_start/end`, `certification_start/end DATE?`, `validation_wape NUMERIC?`, `certified_wape NUMERIC?`, `certified_bias NUMERIC?`, `certification_status TEXT {CERTIFIED,PROVISIONAL,INSUFFICIENT}`, `candidate_status TEXT?`, `artifact_path TEXT?`, `created_at` | FK a run de la misma cadena; rangos ordenados y no superpuestos. `CHALLENGER` no altera Champion. |
| `research_snapshots` | `id UUID PK`, `chain_id UUID`, `cutoff_at TIMESTAMPTZ`, `provider TEXT`, `status TEXT`, `content_hash CHAR(64)`, `summary_json JSONB`, `created_at`, `frozen_at TIMESTAMPTZ?` | FK a cadena. Cada fuente incluida lleva `published_at <= cutoff_at`; contenido original grande en Storage privado. Snapshot congelado inmutable. |
| `forecast_vintages` | `id UUID PK`, `run_id UUID`, `chain_id UUID`, `objective TEXT`, `issue_period DATE`, `cutoff_at TIMESTAMPTZ`, `forecast_version TEXT`, `champion_model_version_id UUID?`, `research_snapshot_id UUID?`, `certification_status TEXT`, `legacy_id TEXT?`, `frozen_at TIMESTAMPTZ?`, `created_at` | FK a run, modelo y research de la misma cadena; `legacy_id` único si existe. Nuevo run crea nuevo vintage. |
| `forecast_horizons` | `id UUID PK`, `vintage_id UUID`, `product_id UUID`, `target_period DATE`, `horizon INTEGER`, `statistical_value NUMERIC?`, `ml_value NUMERIC?`, `ensemble_value NUMERIC?`, `forecast_towell NUMERIC`, `operational_value NUMERIC?`, `model_strategy TEXT`, `confidence TEXT?`, `p10/p50/p90/p95 NUMERIC?`, `band_basis TEXT?`, `band_observations INTEGER?`, `forecast_status TEXT`, `certification_status TEXT`, `created_at` | Producto de la cadena del vintage. `1<=horizon<=12`; único `(vintage_id,product_id,horizon)`; `target_period=issue_period+horizon`; pronósticos no negativos; si hay bandas, `p10<=p50<=p90<=p95`. |
| `forecast_aggregates` | `id UUID PK`, `vintage_id UUID`, `level TEXT {CATEGORY,CHAIN}`, `category_id UUID?`, `target_period DATE`, `horizon INTEGER`, `forecast_towell NUMERIC`, `p10/p50/p90/p95 NUMERIC?`, `created_at` | FK a vintage/categoría de la misma cadena. `CATEGORY` exige categoría; `CHAIN` exige `category_id=NULL`. Unicidad `(vintage_id,level,category_id,horizon)` tratando `NULL` como un valor para el nivel CHAIN. |
| `actual_evaluations` | `id UUID PK`, `vintage_id UUID`, `product_id UUID`, `horizon INTEGER`, `target_period DATE`, `forecast_value NUMERIC`, `actual_value NUMERIC`, `actual_observation_id UUID?`, `evaluated_at TIMESTAMPTZ` | FK a horizonte/vintage, producto y versión real utilizada. Evaluación posterior, separada del vintage. |
| `performance_metrics` | `id UUID PK`, `vintage_id UUID`, `chain_id UUID`, `product_id UUID?`, `horizon INTEGER?`, `metric TEXT`, `phase TEXT {VALIDATION,CERTIFICATION,LIVE,OPERATIONAL}`, `value NUMERIC?`, `period DATE`, `created_at` | FK de misma cadena. `WAPE` necesita `phase`: jamás colapsar `validation_wape`, `certified_wape` y `live_wape` en una cifra. `FILL_RATE=DELIVERY/ORDER`; denominador cero produce estado no calculable, no cero ficticio. |

Antes de `frozen_at`: para cada horizonte, suma de productos por categoría = agregado de categoría, y suma de categorías/productos = agregado de cadena, con política de redondeo única y tolerancia declarada. Una discrepancia bloquea el freeze. `forecast_horizons`, agregados, modelos y research ligados a un vintage congelado son inmutables. Las bandas sólo existen cuando hay residuales/evidencia suficiente; no se fabrican percentiles.

### Gobierno, operación y seguridad

| Entidad | Campos tipados principales | FK y unicidad |
|---|---|---|
| `champion_registry` | `id UUID PK`, `chain_id UUID`, `objective TEXT`, `scope_type TEXT {CHAIN,CATEGORY,PRODUCT}`, `scope_id UUID`, `model_version_id UUID`, `strategy TEXT`, `published_at TIMESTAMPTZ`, `published_by UUID`, `valid_from TIMESTAMPTZ`, `valid_to TIMESTAMPTZ?`, `status TEXT {ACTIVE,RETIRED}` | FK a cadena/modelo/usuario. Para `CHAIN`, `scope_id=chain_id`; para otros scopes debe existir categoría/producto de la misma cadena. Máximo un `ACTIVE` por `(chain_id,objective,scope_type,scope_id)`. Sólo `ChampionRegistry.publish()` autorizado y transaccional puede cambiarlo; ningún trigger lo promueve automáticamente. |
| `forecast_decisions` | `id UUID PK`, `vintage_id UUID`, `product_id UUID`, `target_period DATE`, `version_no INTEGER>0`, `forecast_towell NUMERIC`, `adjusted_value NUMERIC?`, `approved_value NUMERIC?`, `reason_code TEXT`, `comment TEXT?`, `created_by UUID`, `created_at`, `approved_by UUID?`, `approved_at TIMESTAMPTZ?` | FK a vintage/producto/usuarios; único `(vintage_id,product_id,target_period,version_no)`. Nueva decisión crea otra versión. `forecast_towell` coincide con horizonte congelado y no lo actualiza. |
| `jobs` | `id UUID PK`, `chain_id UUID?`, `job_type TEXT`, `run_id UUID?`, `status TEXT`, `requested_by UUID?`, `requested_at TIMESTAMPTZ`, `started_at TIMESTAMPTZ?`, `finished_at TIMESTAMPTZ?`, `error_code TEXT?`, `metadata_json JSONB` | FK a cadena/run/usuario cuando corresponda; alcance de cadena explícito para jobs sin run; transiciones auditadas. |
| `run_logs` | `id UUID PK`, `job_id UUID?`, `run_id UUID?`, `step TEXT`, `status TEXT`, `message TEXT`, `metadata_json JSONB`, `created_at` | FK a job/run; append-only, sin secretos, tokens ni contenido privado de archivos. |
| `audit_log` | `id UUID PK`, `chain_id UUID?`, `actor_id UUID?`, `action TEXT`, `entity_type TEXT`, `entity_id UUID`, `old_data JSONB?`, `new_data JSONB?`, `request_id TEXT?`, `created_at` | Cadena nula sólo para eventos globales; append-only y con redacción de secretos. Eventos mínimos: `IMPORT_CONFIRMED`, `FCST_CLIENT_FROZEN`, `FORECAST_RUN_STARTED`, `CHAMPION_PROMOTED`, `DECISION_APPROVED`, `USER_ROLE_CHANGED`. |
| `profiles` | `id UUID PK/FK auth.users.id`, `full_name TEXT`, `status TEXT`, `global_role TEXT {ADMIN,EDITOR,VIEWER}`, `created_at`, `updated_at` | Relación 1:1 con Supabase Auth; no hay passwords locales. |
| `user_chain_access` | `user_id UUID`, `chain_id UUID`, `can_view/edit/import/run_forecast/approve BOOLEAN`, `created_at` | PK o único `(user_id,chain_id)`; FK a `profiles` y `chains`. Roles globales no sustituyen permisos de cadena. |

### Relaciones de lineage y migración

`forecast_run_inputs` (relación lógica; materialización en 09.2B) liga `run_id` a `monthly_observation_id`, `customer_forecast_version_id?` cuando se use como benchmark, `source_evidence_id?` y el hash del snapshot. Debe permitir reconstruir exactamente las versiones conocidas al cutoff sin duplicar todos los hechos. `legacy_identity_map` (herramienta de migración, no fuente operacional) conserva `(entity_type,legacy_id,new_uuid,source_hash,imported_at)` con unicidad por tipo/id legacy. La observación se rastrea `observation → batch → storage_path + sha256 → uploaded_at + uploaded_by`; E3 además conserva manifiesto, corte y hash originales.

## Seguridad y Storage (contrato para 09.2C)

RLS debe estar habilitado para toda tabla expuesta a usuario; `anon` no obtiene lectura operacional. `VIEWER` puede consultar sólo cadenas con `can_view`; `EDITOR` necesita el booleano de acción correspondiente; `ADMIN` tiene alcance global tras verificación de JWT, rol y vigencia. FastAPI valida identidad y permisos de nuevo: los headers/roles enviados por el navegador no son autoridad. La operación de usuario usa contexto `UserScopedClient`; `ServiceClient` sólo para jobs de fondo, cálculos del sistema y migraciones controladas, nunca para elevar una solicitud arbitraria. La publicación de Champion y aprobación de decisiones requieren autorización explícita, transacción y auditoría.

Buckets privados previstos: `source-files`, `research-evidence`, `model-artifacts`, `exports`. Ruta de origen: `chain_id/YYYY-MM/batch_id/original-filename`; el nombre se sanitiza. Acceso por RLS o URL firmada de vida corta según el caso. Ningún archivo privado, clave de servicio, token ni export con datos privados se guarda en Git o frontend.

## Índices y restricciones para 09.2B

- Índices: `products(chain_id,product_code)` y `(chain_id,category_id)`; `monthly_observations(chain_id,period)`, `(product_id,period)`, `(product_id,metric_code,period)`; `customer_forecast_versions(chain_id,issue_period)` y rows `(product_id,target_period)`; `forecast_vintages(chain_id,issue_period)`; horizons `(vintage_id,product_id,horizon)` y `(product_id,target_period)`; métricas `(chain_id,period)` y `(product_id,period)`.
- Unicidad con `NULL`: variante ausente y agregado CHAIN necesitan índices/constraints que traten `NULL` de manera explícita. La cadena de un FK compuesto debe coincidir con la entidad padre. El `scope_id` polimórfico de Champion requiere validación de integridad de su tipo. Los mecanismos SQL concretos pertenecen a 09.2B.
- Fechas mensuales alineadas, valores no negativos donde aplique, horizonte 1–12, bandas ordenadas, versiones positivas, transición de estados válida y prohibición de escritura sobre frozen son restricciones de base **y** validaciones de dominio. Ningún trigger promueve Champion ni infiere `available_at`.

## Migración y cutover (contrato para 09.2F/09.2G)

1. Inventariar entidades SQLite, archivos y evidencia; identificar versiones y datos `UNKNOWN/BLOCKED` sin modificarlos.
2. Exportar a artefactos privados con hashes; transformar IDs mediante `legacy_identity_map`; no subir exportaciones al repositorio.
3. Importar en staging, no producción. Reconciliar conteos, hashes, fuente E3, 346 celdas verificadas, cortes, vintages congelados, 12 horizontes, Champion, jobs y métricas. Un fallo bloquea cutover.
4. Probar read/write, rollback, permisos/RLS, backup/restore, continuidad tras restart/redeploy y equivalencia del motor con `PersistenceProvider`/`DataProvider` locales y hospedados.
5. Sólo tras aprobación y reconciliación: `PERSISTENCE_PROVIDER=supabase`, `DATA_PROVIDER=supabase`, `SUPABASE_ENABLED=true`. SQLite queda para local/tests/fallback técnico, sin doble escritura permanente.

**Brecha de migraciones existentes:** `supabase/migrations/202609180001_prd02_base.sql` y posteriores son artefactos legacy, no la implementación de este contrato. Hoy definen, entre otras cosas, `products` por `organization_id` con un índice sobre descripción y `public.users` con roles anteriores; eso contradice la identidad por cadena/producto y `auth.users → profiles` aquí fijadas. 09.2B debe diseñar una migración de reconciliación y verificar qué se aplicó realmente antes de ejecutar cualquier SQL. No editar/reaplicar estas migraciones a ciegas. `SupabasePersistenceProvider` y `SupabaseDataProvider` reales se implementarán y probarán en 09.2G, no en este PRD.

## Gate de aprobación de 09.2A

El contrato cubre entidades, PK/FK, unicidad, productos dinámicos, importación versionada, disponibilidad temporal, Fcst Cliente, H1–H12, vintages, Champion, métricas, Research, Auth/roles, RLS, Storage, auditoría, migración y múltiples cadenas. **Aprobar este documento no crea tablas ni conecta Supabase.** 09.2B debe convertirlo en SQL y tests de constraints; 09.2C implementará Auth/RLS/Storage; 09.2D–G implementarán flujos, migración y E2E. Hasta entonces permanecen `SUPABASE_ENABLED=false`, `PERSISTENCE_PROVIDER=sqlite` y `DATA_PROVIDER=normalized` en el runtime actual.
