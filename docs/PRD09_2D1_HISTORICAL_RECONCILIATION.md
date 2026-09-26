# PRD 09.2D.1 — Reconciliación histórica multichain

Estado: **BLOCKED_BY_RECONCILIATION**. La implementación y el análisis están completos; no se autoriza una carga parcial ni masiva mientras existan discrepancias bloqueantes. No se escribió en Supabase ni se cambió el runtime, la interfaz, los modelos o los snapshots/vintages congelados.

## Gates previos

Gate 0: main remoto y árbol limpio en `071439933e41990badb2b79cf71fd9a287778c6d`. [CI base PASS](https://github.com/towell2026ia/Towell_Forecast/actions/runs/36199648592).

Gate 1: nueva autenticación CLI; proyecto vinculado `bskoyqhbgrycpwhydnnr`. Inspección exclusivamente READ-ONLY: migraciones 001–007 aplicadas, 28 tablas, RLS 28/28, cuatro buckets privados (`source-files`, `research-evidence`, `model-artifacts`, `exports`). Cadenas, productos, observaciones, batches y objetos de `source-files`: todos 0. Sin diferencias con el gate aprobado. No se ejecutaron migraciones, semillas ni carga remota.

## Metodología y reglas públicas

El scanner anterior sigue disponible para compatibilidad. `--hardened --baseline` activa la reconciliación 09.2D.1 y exige explicar cada conflicto del reporte original, con sus fuentes originales intactas. Los hashes del conjunto de fuentes deben coincidir.

Nivel A: celdas numéricas literales del detalle, con identidad, cadena, periodo y métrica verificables. Las tablas anchas ITEM-only sólo se enlazan mediante pares explícitos de identidad, nunca por descripción. Nivel B es el papel de un detalle A posterior **compatible y validado**; no constituye una licencia para usar cualquier archivo más reciente. Nivel C: literales de resumen. Nivel D: fórmulas/lookup/pivot/agregado. Nivel E: inferencia por nombre. A tiene precedencia sobre C/D/E. Un resumen aislado sin detalle no se publica como hecho.

No se recalculan fórmulas ni se confía en sus valores cacheados. Se leen los libros sin modificarlos. No se suman duplicados. Entre valores iguales se escoge una fuente reproducible por hash/hoja/celda; ese orden nunca decide entre valores distintos. Copias de archivo con el mismo SHA tienen una sola representación.

Reason codes explícitos: `EQUIVALENT_DUPLICATE`, `DIRECT_VS_SUMMARY`, `VALIDATED_REVISION`, `SAME_SNAPSHOT_CONFLICT`, `INTERNAL_SOURCE_DUPLICATE`, `FORMULA_UNVERIFIED`, `ITEM_MULTI_UPC`, `ITEM_ONLY_AMBIGUOUS`, `DESCRIPTION_ALIAS`, `PRE_LAUNCH_ZERO`, `UNPROVEN_ZERO`, `MISSING`, `INVALID_VALUE`, `CROSS_SOURCE_CONFLICT`, `INSUFFICIENT_EVIDENCE`. Se añaden `AGGREGATE_ONLY`, `DIRECT_FACT` y `CONFIRMED_ZERO` para exclusiones de hoja y hechos aceptados. Ninguna discrepancia desaparece bajo un catch-all silencioso.

Una revisión exige ambos detalles A, identidad inequívoca, misma cadena/producto/periodo/métrica, secuencia comparable, prueba del corte posterior y ausencia de contradicción interna o fórmulas. `SnapshotProof` es un registro **revisado por un operador backend confiable**, ligado al hash de la fuente y de la evidencia, locator, secuencia, corte, aprobación y nota. No existe entrada de browser, filename o timestamp que lo cree automáticamente. El scanner CLI de este corpus no recibió pruebas de secuencia/corte comparables, por lo que no declara revisiones legítimas. Que un archivo diga “cierre” no es prueba suficiente.

Las revisiones conservan V1/V2 y seleccionan el valor de la última versión validada en el preview. Su disponibilidad sigue siendo UNKNOWN salvo evidencia temporal independiente. La prueba de corte y la prueba de disponibilidad son contratos distintos.

Los ITEM compartidos no fusionan UPC. Se preservan 20/20 casos multicode en el ledger de identidad. Una fila con UPC explícito se mantiene inequívoca; una fila ITEM-only multicode sólo puede enlazarse en un periodo explícitamente observado con un único UPC. No se extrapolan intervalos, huecos o vigencia de catálogo desde first/last observed. Dos mappings aplicables, o ausencia de mapping temporal defendible en un ITEM multicode, bloquean esa fila. La descripción es alias, nunca identidad.

La actividad se demuestra sólo con valores positivos de detalle A. Ceros anteriores se excluyen; productos sólo cero sin otra evidencia se excluyen y no crean productos. Faltantes, errores, negativos y fórmulas no crean observaciones. Un agregado no se expande en historia de SKU. El corpus 2023 válido procede del detalle encontrado en fuentes posteriores; no se fabrica cobertura uniforme para otras cadenas.

## Destino de los 730 conflictos originales

| Clasificación | Conflictos |
| --- | ---: |
| AUTO_RESOLVABLE | 18 |
| RESOLVABLE_AS_REVISION | 0 |
| EXCLUDED_NON_FACT | 0 |
| IDENTITY_BLOCKED | 0 |
| VALUE_BLOCKED | 712 |
| **Total** | **730** |

| Causa | 2024 | 2025 | Total |
| --- | ---: | ---: | ---: |
| DIRECT_VS_SUMMARY | 18 | 0 | 18 |
| INTERNAL_SOURCE_DUPLICATE | 464 | 2 | 466 |
| SAME_SNAPSHOT_CONFLICT | 1 | 0 | 1 |
| CROSS_SOURCE_CONFLICT | 0 | 245 | 245 |
| **Total** | **483** | **247** | **730** |

La causa primaria interna tiene prioridad cuando una misma fuente contiene detalles contradictorios; no se oculta como empate entre archivos. Las 464 claves internas de 2024 incluyen discrepancias entre detalles del mismo libro: no se presume que una de esas hojas represente una revisión oficial. Las 245 diferencias entre fuentes podrían ser revisiones, pero no se conocen secuencias comparables y cortes suficientemente acreditados. Hace falta evidencia objetiva, no escoger max/first/last ni ordenar nombres. Hay 710 claves bloqueadas en Walmart y 2 en Al Super. No hay ITEM-only ambiguos en los hechos numéricos escaneados; no se han fusionado los UPC multicode.

## Historical bootstrap preview (NO cargado)

| Concepto | Resultado |
| --- | ---: |
| Archivos / hashes únicos | 6 / 5 |
| Cadenas | 13 |
| Identidades preliminares | 914 |
| Productos canónicos | 894 |
| Categorías chain-scoped detectadas | 45 |
| SALES | 12,385 |
| ORDER | 12,240 |
| DELIVERY | 12,179 |
| Observaciones / filas de versión | 36,804 / 36,804 |
| Revisiones validadas | 0 |
| Periodo mínimo / máximo | 2023-01 / 2026-07 |
| Ceros confirmados canónicos | 8,443 |
| Decisiones equivalentes, incluida copia SHA | 8,345 |
| Fuentes adicionales con el mismo valor canónico (no sumadas) | 13,009 |
| UNKNOWN / E1 / E2 / E3 | 36,804 / 0 / 0 / 0 |
| available_at fabricado | 0 |

La diferencia respecto de 36,858 hechos anteriores es +18 detalles resueltos y −72 literales derivados sin detalle A, dando 36,804. Los 20 productos preliminares no incluidos carecen de hechos canónicos elegibles; no se inventa estado de catálogo.

Exclusiones trazadas: 9,412 missing; 8,877 pre-launch zero; 438 unproven zero; 492 fórmulas; 13 valores inválidos; 72 grupos de literales derivados sin detalle suficiente. Se inventariaron 57 hojas sin perfil de actual de producto (`AGGREGATE_ONLY` cuenta **hojas**, no filas ni valores). Una hoja no reconocida se conserva en inventario; su exclusión no certifica que esté vacía ni que todos sus contenidos sean agregados. Estas exclusiones seguras no bloquean por sí solas la publicación. Sí bloquean las 712 discrepancias reales.

## Cobertura agregada por cadena

| Cadena | Productos | Desde | Hasta | Observaciones 2023 / 2024 / 2025 / 2026 |
| --- | ---: | --- | --- | --- |
| Al Super | 45 | 2025-01 | 2026-07 | 0 / 0 / 1,321 / 420 |
| Casa Ley | 54 | 2025-01 | 2026-07 | 0 / 0 / 1,548 / 579 |
| Chedraui | 128 | 2025-01 | 2026-07 | 0 / 0 / 3,777 / 2,526 |
| City Fresko | 51 | 2025-01 | 2026-07 | 0 / 0 / 726 / 408 |
| DSW | 47 | 2025-01 | 2026-07 | 0 / 0 / 1,032 / 819 |
| GS Sears | 28 | 2026-03 | 2026-07 | 0 / 0 / 0 / 420 |
| HEB | 79 | 2025-01 | 2026-07 | 0 / 0 / 924 / 603 |
| Liverpool | 38 | 2025-01 | 2026-07 | 0 / 0 / 807 / 741 |
| Merco | 8 | 2025-01 | 2026-07 | 0 / 0 / 276 / 84 |
| Soriana | 85 | 2025-01 | 2026-07 | 0 / 0 / 2,688 / 1,470 |
| Suburbia | 53 | 2025-01 | 2026-07 | 0 / 0 / 1,643 / 993 |
| TresB | 23 | 2025-04 | 2026-07 | 0 / 0 / 333 / 12 |
| Walmart | 255 | 2023-01 | 2026-07 | 3,596 / 4,563 / 3,037 / 1,458 |

Los 0 en esta tabla son **conteos de observaciones disponibles**, no demanda imputada. No existe regla de producción especial para ninguna cadena/producto. FENDI sólo se usa como golden privado: **96/96 comparables PASS, 0 diferencias**, con cobertura exacta exigida por `--expected-count 96`. Muestra automática de un detalle por cada una de 13 cadenas: PASS, 0 diferencias (incluye 12 cadenas distintas del golden).

## Temporalidad, lineage y SQL

Inspeccionados `legacy_identity_map`, `source_evidence`, `import_batches`, `monthly_observations` en 001–007. El contrato existente permite hash/batch, identidad genérica, append-only, version_no y evidencia. No se crea 008 ni se modifican 001–007 por conveniencia. Las equivalencias y decisiones detalladas permanecen en los ledgers privados; un futuro adaptador puede enlazar múltiples locators mediante `legacy_identity_map`, validando los UUID de destino.

UNKNOWN conserva NULL. E1/E2/E3 sólo desde registro backend validado ligado a cadena/hash/evidence_id, documento privado y fechas verificadas; nunca se acepta una fecha manual del navegador. No se suministró registro E1/E2/E3 para este corpus. La fecha actual de recepción de un batch puede ser SYSTEM_INGESTION **del batch**, nunca de las observaciones históricas. La vista SQL `monthly_observations_current` excluye UNKNOWN: no se cambia para mostrar estos hechos como conocidos. El `selected` del preview es current de **valor**, no demuestra disponibilidad para backtesting.

`publication_preflight` exige cero bloqueos, golden, tests, proyecto exacto y gate remoto; comprueba integridad hash del dataset. El port server-only `HistoricalBootstrapRepository` y el coordinador prueban idempotencia/checkpoint/transacción/reanudación con un adaptador SQLite **sólo de test**. No constituye certificación de PostgreSQL ni un adaptador Supabase productivo. Como el gate real está bloqueado, Storage adapter = NOT REQUIRED, Postgres transaction = BLOCKED. No hay endpoint público ni conexión runtime. Antes de desbloquear habrá que implementar/certificar el adaptador real, autorización server-only, verificación de hashes, transaction + checkpoint por batch, orden chains/categories/profiles/batches/products/evidence/observations conforme FK, validación de lineage y conteos post-carga; nunca DELETE ni sobrescritura.

Runtime conservado: `SUPABASE_ENABLED=false`, `PERSISTENCE_PROVIDER=sqlite`, `DATA_PROVIDER=normalized`. OpenAI, Deep Research y Voice siguen desactivados; no se configuró Netlify ni Railway.

## Artefactos privados y repetición

En `outputs/prd09_2d1/` (ignorado): `reconciliation.json`, `resolution_ledger.json`, `identity_ledger.json`. Contienen valores/identidades/locators/hashes/versiones/decisiones; **no se deben publicar ni subir a Git**. El reporte original `outputs/prd09_2d/reconciliation.json` permanece intacto; SHA-256 `0a0db2ceb09cc8ccbf41b574cff019091b919380da164e165149011f12946cc3`. Se exige un directorio nuevo en cada repetición para no reemplazar evidencia previa.

```powershell
python scripts/scan_historical_corpus.py --hardened `
  --baseline outputs/prd09_2d/reconciliation.json `
  --source '<fuente 2023.xlsx>' `
  --source '<fuente 2024.xlsx>::<cadena>::2024::2024-12' `
  --source '<otra fuente 2024.xlsx>::<cadena>::2024::2024-12' `
  --source '<fuente febrero 2025.xlsx>::<cadena>::2025::2025-02' `
  --source '<fuente julio 2026.xlsx>' --source '<copia julio 2026.xlsx>' `
  --output outputs/prd09_2d1/repeat/reconciliation.json
python scripts/check_historical_golden.py --report '<reporte privado>' `
  --facts '<facts privados>' --chain '<cadena>' --year 2024 `
  --product-prefix '<prefijo del golden privado>' --expected-count 96
```

Éxito del scanner significa **reporte producido**, no autorización de escritura; consultar siempre `blocking` y `publication_preflight`. Las reglas viven en Git; no se usaron overrides. Cualquier futura excepción requiere archivo privado revisado con source hash + locator, decisión, razón, approved_by/nota; no se acepta silenciosamente ni se convierte en hardcode de UPC. Nuevas pruebas revisadas de snapshots/evidencia se suministran desde un registro backend confiable, no desde el runtime público.

## Verificación

48 tests nuevos (CP01–CP36 más seguridad/regresión adicional); suite completa assistant, ingesta mensual CSV/XLSX/SHA/preview/productos/versiones/rollback y pruebas de motores. SQL baseline y seguridad se ejecutan contra PostgreSQL local aislado (PGlite); no escriben en Supabase real. El test sintético de 96 hechos verifica el mecanismo, y **no sustituye** el golden privado real ejecutado sobre las 96 observaciones. RLS y Storage privados se verificaron además remotamente en Gate 1. Docker y smoke de reinicio se verifican en CI; Docker no está instalado en el host local.

Resultados locales: assistant 136/136; motores 110/110 (estadístico 12, ML 11, ensamble 18, decisión 30, cierre 25, genérico 14): **246/246 PASS**. Cobertura assistant **84%**, reconciliador **97%**, scanner **93%** (gate CI ≥81%). SQL baseline PASS, seguridad 21/21 y RLS 28/28 PASS; auditoría de forecast PASS; lint/typecheck/build PASS. Advertencias existentes: cuatro avisos lint en archivos locales del entorno Python y avisos de deprecación/tamaño de bundle, sin errores. Segunda ejecución invirtiendo el orden de las seis fuentes produce exactamente las mismas versiones, conteos y SHA-256 canónico `7bcb9d459fd30b8e05e2f40e82a0eeb8bcf57128ec42a3342407df873c07fbc9`. Hash del baseline original sin cambios.

Último inventario remoto verificado en Gate 1: 0 cadenas, 0 productos, 0 observaciones. Una comprobación posterior devolvió 403 y `projects list` mostró otra cuenta, conservando el mismo ref local; se detuvieron las consultas Supabase y se pidió nueva autenticación Towell. No se deduce un cambio de datos desde ese error, ni se considera válida una consulta fallida. Esta CLI compartida exige verificar cuenta/proyecto inmediatamente antes de cualquier operación futura; una credencial de otra cuenta nunca autoriza bootstrap. El próximo paso requiere evidencia objetiva para las 712 discrepancias, no permiso para ignorarlas. Hasta entonces: **NO CARGAR MASIVAMENTE**.
