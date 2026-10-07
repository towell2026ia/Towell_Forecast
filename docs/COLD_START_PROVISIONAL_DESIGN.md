# Forecast provisional de arranque reciente

## Problema observado

Un producto con menos de seis meses consecutivos de venta desde su primera venta positiva no supera `ForecastPolicy.min_product_observations`. Los ceros anteriores al lanzamiento no se usan para fingir antigüedad. Toalla MB Fendi Oxford tiene venta positiva de marzo a julio de 2026 (cinco meses); ahora puede recibir un H1–H12 **provisional**, separado del preview validado.

## Contrato propuesto

- `PROVISIONAL_COLD_START` es un resultado separado para productos nuevos con cero a cinco meses de venta observada y datos de identidad, scope, periodo y cantidad íntegros. Con cero meses la confianza es baja y el nivel procede íntegramente de comparables. No cambia la regla de seis meses del motor validado.
- Los candidatos son (1) baseline de nivel reciente, (2) trayectoria normalizada de productos comparables y (3) Random Forest global de comparables de la **misma cadena y categoría**. Un comparable debe tener al menos 18 meses observados y no puede ser el propio producto objetivo ni otro scope padre/hijo sumado. La selección de comparables y el escalamiento usan exclusivamente información del corte retrospectivo.
- Evaluar candidatos en cohortes históricas de otros productos, simulando la edad de arranque del objetivo. Usar leave-one-product-out y reportar WAPE, Bias y cobertura por H1, H3, H6 y H12 **de los comparables**, con conteos. No atribuir estas métricas a Oxford. Esto no demuestra disponibilidad point-in-time del archivo histórico.
- Elegir el challenger de menor WAPE agregado sólo si mejora al baseline y no deteriora el Bias absoluto más de cinco puntos. Si no mejora, usar baseline provisional; si no hay al menos tres comparables evaluables, mantener `INSUFFICIENT`. No generar doce cifras sin respaldo.
- Guardar en el job provisional: versión de política/modelo, corte, hash del dataset, IDs de comparables, meses observados del producto, métricas de comparables y H1–H12. Etiquetar cada valor como provisional y sin certificación point-in-time cuando `available_at` histórico es desconocido. No escribir `forecast_previews`, vintages ni Champion.
- Deep Research es opcional, sólo en backend. Consulta descriptores del producto y fuentes públicas mediante OpenAI Responses API con `web_search`; nunca recibe ventas, cantidades, precios, IDs internos, tokens o la clave en el navegador. Su texto y citas no cambian las cantidades del modelo. Si falla, el forecast provisional sigue disponible.
- No crear ni promover Champion, vintage oficial, decisión de pedido ni publicación automática a partir de este resultado. La UI debe distinguirlo de `Fcst Towell` validado y permitir auditar sus insumos; no mostrar WAPE del producto objetivo cuando no existe.

## Activación y controles

El cálculo numérico provisional se activa en el backend operacional para productos seleccionados con comparables válidos. No se ejecuta automáticamente al navegar: el usuario autorizado pulsa «Calcular vista previa». La investigación permanece desactivada por defecto. Para habilitarla en Railway, capturar `OPENAI_API_KEY` **sólo** como secreto del servicio backend y configurar `OPENAI_ENABLED=true` y `DEEP_RESEARCH_ENABLED=true`; nunca en Netlify, `NEXT_PUBLIC_*` o Git. Mantener `VOICE_ENABLED=false` y los flags de publicación oficial sin cambio. Si falta la clave o los flags no coinciden, el servicio falla al validar configuración.

## Gate de verificación

1. Pruebas de fuga temporal, identidad de cadena/categoría, ceros prearranque, meses faltantes y ausencia de comparables.
2. Backtest de cohortes reales por cadena con comparación contra baseline y métricas por horizonte; reportar también fallos, no sólo ganadores. El backtest sintético no reemplaza esta aceptación productiva.
3. Pruebas de autorización y de no escritura histórica/oficial.
4. Verificación en Railway con usuario autenticado y un preview real; revisar Oxford sólo después del deploy del backend vigente.

La aceptación productiva de Oxford requiere verificar el nuevo SHA de Railway y una corrida autenticada que muestre los doce horizontes y el número de comparables. Hasta esa prueba, no atribuir un PASS de Oxford real.
