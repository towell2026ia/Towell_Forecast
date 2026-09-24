# PRD 09.1B — Motor genérico y certificación

## Alcance implementado

La ruta operativa de `create_app()` usa `GeneralizedMonthlyForecastRunner`. El motor nuevo (`services/forecast_engine`) acepta N cadenas y productos, separa `Venta`, `Pedido` y `Entrega`, y produce un registro producto × H1–H12. Categoría y cadena son sumas reconciliadas de esos registros; no sustituyen el pronóstico del producto. `Fcst Cliente` no es un target: queda fuera de este entrenamiento y conserva su papel de benchmark externo.

`ExistingEnginePipeline`, `MonthlyForecastRunner` y `HistoricalForecastRunner` permanecen como compatibilidad del piloto y de la evidencia congelada. No se modificaron snapshots, vintage E3, archivos de datos ni decisiones anteriores. El nuevo motor no llama sus funciones de carga/totalización con reglas FENDI. La auditoría de nombres cubre el núcleo nuevo; **no afirma que todo el código de compatibilidad histórica esté libre de referencias al piloto**.

## Contrato normalizado

Cada observación requiere `chain_id` (o alias `chain_code`/`chain`), `product_id` (o `canonical_product_id`), `period`, `objective`, `value`, `is_missing` y `available_at`. `product_code`, `description`, `category` y `variant` son atributos, nunca sustitutos de identidad. Un conflicto de duplicados se rechaza. Dato ausente no se convierte en cero. Los ceros anteriores al primer movimiento se excluyen; los posteriores se conservan. `available_at` posterior al corte y fuentes de research posteriores al corte se rechazan antes del cálculo.

El CSV piloto comprometido en `app/data` **no tiene `available_at`**. Por ello, un nuevo run certificado sobre ese CSV falla con `availability_metadata_missing`: no se inventan fechas ni se desbloquean los registros 2024. La prueba de dos cadenas utiliza únicamente datos sintéticos explícitos, no evidencia productiva ni datos históricos reales adicionales.

## Tiempo, modelos y métricas

La primera ventana es TRAIN (mínimo 18 meses de historia de la cadena, ampliable a 30 si hay tiempo suficiente). Los mínimos de entrenamiento, selección y certificación son configurables con `ForecastPolicy` (valores iniciales 18/6/3). En TRAIN se ajustan parámetros SES, Holt, Holt-Winters, Croston, SBA y TSB por búsqueda acotada. La ventana siguiente usa todos los periodos objetivo elegibles para VALIDATION; allí se elige el modelo estadístico por producto, el modelo ML global y el peso del ensamble sobre observaciones comunes. Los tres últimos **periodos objetivo** elegibles quedan sellados para CERTIFICATION; la separación se hace por target global para que un mismo real no aparezca en validación a H12 y certificación a H1. Ningún real de esa ventana participa en el ajuste o en la elección. El preprocesador ML se ajusta únicamente con muestras de TRAIN para evaluar validación/certificación. Para emitir el pronóstico operativo futuro, el modelo ganador se reentrena con el histórico ya conocido al corte.

Modelos estadísticos iniciales: Naive, Naive estacional, medias móviles, SES, Holt, Holt-Winters, regresiones y Croston/SBA/TSB. Modelos ML iniciales: regresión lineal, Random Forest y Gradient Boosting globales. XGBoost/LightGBM siguen deshabilitados. Las variables ML se derivan de calendario, horizonte, lags, ventanas e identidad conocida en TRAIN; ninguna variable objetivo posterior al issue se incorpora.

`model_audit` registra rangos, candidatos, parámetros, Champion anterior, Challenger, orígenes, observaciones, WAPE de validación, WAPE certificado y métricas por horizonte. `training_wape` y `live_wape` se dejan explícitamente en `null` hasta existir esos diagnósticos; no se sustituyen con WAPE de otro conjunto. Cuando faltan ventanas o denominador de demanda, se reporta `PROVISIONAL`/`INSUFFICIENT`, no un WAPE certificado ficticio. Un producto nuevo es `COLD_START` y `PROVISIONAL`.

Las bandas P10/P50/P90/P95 salen de residuales de certificación comparables por producto y horizonte; si no hay residuales del producto se usan, de forma identificada, categoría y después cadena. Si tampoco hay observaciones comparables, las bandas quedan `null`, no se fabrican multiplicadores. `band_basis` y `band_observations` indican la evidencia utilizada.

## Champion y persistencia

`ChampionRegistry` consulta/publica por `chain_id × objective × scope` a través de `PersistenceProvider`. Un nuevo run lee el Champion anterior, incluso si era ML, y no lo reemplaza automáticamente. Sin Champion, la salida muestra `INITIAL_CHAMPION_CANDIDATE`, no una publicación. El candidato elegible queda `CHALLENGER`; sólo `publish(..., authorized=True, actor=...)` modifica el registro. La capa que invoque `publish` debe verificar previamente la identidad/permiso real del actor; no hay ruta pública de promoción nueva en este PRD.

El runner persiste el vintage, auditoría y 12 horizontes por producto mediante el proveedor abstracto. Si el cómputo falla, sólo reutiliza un vintage persistido cuyo `version` coincide con el Champion publicado. No emite un vintage nuevo para ese fallback. Errores de disponibilidad o research fallan cerrados. SQLite sigue siendo la implementación local; no se añadió proveedor Supabase.

## Certificación y límites de esta entrega

Ejecutar `python scripts/audit_forecast_engine.py` para la prueba sintética de dos cadenas, productos regular/intermitente/estacional/nuevo, H1–H12, Champion previo, Challenger, holdout y reconciliación. El script devuelve código distinto de cero al fallar un control y se ejecuta en CI junto con los tests. Esta prueba certifica el **contrato y cálculo sobre fixtures**, no la precisión productiva del piloto. La certificación real por cadena/producto necesita fuentes normalizadas con disponibilidad temporal demostrada y al menos las ventanas mínimas.

Pendientes fuera del alcance de este PRD: migrar el almacenamiento a Supabase, activar el asistente/Deep Research/voz, conectar el Site al backend y publicar automáticamente un Champion. La verificación de Railway y sus endpoints debe realizarse sobre el commit desplegado, no inferirse del éxito de CI.
