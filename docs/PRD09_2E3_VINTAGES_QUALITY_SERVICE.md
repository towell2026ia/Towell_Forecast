# PRD 09.2E.3 — Vintages, quality gates y servicio

Estado de implementación: **pre-cutover**. Ningún flag de escritura E3 se activa por defecto. Este documento no autoriza crear el primer vintage real ni cambiar variables de Railway.

## Ciclo de vida

1. Un preview E2 existente y `READY_PREVIEW` es la única fuente. Navegar o consultar gates no vuelve a ejecutar modelos.
2. El backend compara el hash del dataset actual con el del preview. Si cambió, exige un preview nuevo.
3. Se evalúan por separado Data Quality, Forecast Quality y Service Level con política `E3-GATES-1.0.0`.
4. Un ADMIN autenticado puede solicitar candidato con `Idempotency-Key`. Una única función SQL inserta run, modelos, vintage, 12 horizontes por producto elegible, agregados, inputs, métricas retrospectivas, gates y auditoría. `run_code` derivado del hash evita duplicados del mismo contenido.
5. Freeze es otra acción explícita. El guard SQL exige H1–H12, agregados reconciliados y lineage; los horizontes/vintage congelados son inmutables.
6. Publicación oficial y Champion son acciones independientes, cada una con flag propio y confirmación. Los datos retrospectivos con `available_at=NULL` son **PROVISIONAL**, nunca certificados point-in-time ni publicables bajo la política inicial. Champion requiere modelo `CERTIFIED`, no degradación y transacción que retire el anterior antes de insertar el nuevo.
7. El cierre mensual LIVE es una acción ADMIN explícita (`POST /api/forecast/vintages/{id}/close`) sobre un vintage congelado y un mes objetivo H1–H12. Requiere observaciones reales `available_at` conocidas, lote de ingesta confirmado o evidencia válida, y calcula WAPE, Bias, MAE, RMSE y Fill Rate observado en transacción. Es idempotente, no altera el vintage, no publica y no promueve Champion. La ruta permanece deshabilitada mientras `VINTAGE_PERSISTENCE_ENABLED=false`.

La migración `202609290010_e3_vintage_transaction.sql` agrega **una** tabla, `forecast_quality_gates`, append-only/RLS, y un campo `evidence_mode` a `forecast_run_inputs`. Este campo resuelve una incompatibilidad del baseline: el guard previo sólo permitía inputs con `available_at` conocido. `RETROSPECTIVE_TRAINING` acepta lineage FK de observaciones UNKNOWN **únicamente** para vintage `PROVISIONAL`; el modo `POINT_IN_TIME` conserva el bloqueo anterior. La migración también define RPC transaccionales, incluido el cierre LIVE, ejecutables exclusivamente por `service_role`. No modifica observaciones ni fechas históricas. El cierre LIVE se añadió **sólo como código local autorizado**: no ejecutar `db push` ni activar flags sin un gate remoto posterior.

## Políticas de calidad

La política inicial configurable es: 18 meses de referencia, continuidad warning 0.85, ready 0.95, objetivo de Fill Rate 95.0%. Las métricas por scope incluyen meses observados/esperados/faltantes, continuidad, gaps, productos visibles/con historia/elegibles, identidades duplicadas, negativos y `available_at` desconocido. Un producto nuevo no invalida por sí solo un scope. La calificación de histórico incompleto requiere evidencia de gaps observables; una ausencia anterior al primer registro no se infiere ni se fabrica.

El guard de freeze queda deliberadamente fijado a `E3-GATES-1.0.0`. No cambiar thresholds en producción bajo esa misma versión: un cambio de política requiere versión nueva y revisión explícita del guard SQL antes de congelar vintages con ella.

Forecast Quality compara WAPE y |Bias| del leader retrospectivo con el baseline estadístico del **mismo scope**. Si no hay referencia se informa `NO_REFERENCE`; no existe umbral absoluto común de WAPE ni baseline FENDI. La comparación por horizonte sólo se informa cuando el preview contiene evidencia para ello. No se modifica el catálogo ni el algoritmo E2.

Observed Fill Rate se calcula como `sum(Delivery)/sum(Order)*100` sobre pares realizados por producto/periodo, nunca como promedio de porcentajes. Pedido=0 y Entrega=0 da 100%; Pedido=0 y Entrega>0 o pares incompletos son `NOT_MEASURABLE`. El target de 95% y su brecha se muestran separados del forecast. Un Fill Rate pasado bajo no bloquea por sí mismo Forecast Quality. No se promete Fill Rate futuro, no se interpreta P95 como garantía y `operational_value` permanece NULL sin política de inventario/simulación.

## Seguridad y flags

```text
VINTAGE_PERSISTENCE_ENABLED=false
OFFICIAL_PUBLICATION_ENABLED=false
CHAMPION_PUBLICATION_ENABLED=false
SERVICE_TARGET_FILL_RATE=95.0
```

El navegador usa sólo JWT de usuario Supabase y clave publicable para lectura; nunca recibe `SUPABASE_SERVICE_ROLE_KEY`. El backend verifica JWT/rol ADMIN/scope antes de invocar RPC. La clave de servicio es variable **servidor Railway únicamente**, sin registro ni exposición. Si falta, `/api/ready` informa `REQUIRED_SECRET_MISSING` cuando la persistencia está habilitada. Un flag no enciende otro.

Para Fase A, desplegar código con los tres flags E3 en `false` y confirmar `/api/ready`, E2 y cero escrituras. La migración 010 debe estar aplicada y validada en el proyecto correcto **antes** de cualquier habilitación de persistencia. Fase B requiere aprobación explícita del owner para `VINTAGE_PERSISTENCE_ENABLED=true`. Mantener official y Champion en `false`. No crear el primer candidate ni congelarlo sin autorización específica.

## Rollback y aceptación real posterior

Rollback funcional: regresar al SHA previo y poner los tres flags en `false`. No borrar vintages, gates ni métricas ya creados. La primera aceptación real debe usar Al Super y el último preview válido de una sola cadena: verificar dataset hash, lineage, 12 horizontes, sumas y estado PROVISIONAL; freeze sólo con aprobación adicional. Un scope con cobertura históricamente incompleta debe seguir provisional y no publicable. No tocar datos de Supabase ni `available_at` para forzar un PASS.

Las pruebas locales de `scripts/test_e3_schema.mjs` son sintéticas en PGlite y no escriben al proyecto Supabase. La migración remota, servicio Railway y aceptación web requieren gates separados antes de declarar E3 PASS.
