# Forecast provisional de arranque reciente (diseño; no activado)

## Problema observado

Un producto con menos de seis meses consecutivos de venta desde su primera venta positiva no supera `ForecastPolicy.min_product_observations`. Los ceros anteriores al lanzamiento no se usan para fingir antigüedad. En el archivo histórico certificado, Toalla MB Fendi Oxford tiene venta positiva de marzo a julio de 2026 (cinco meses); por ello el preview de producto actual debe responder `INSUFFICIENT`, no emitir un H1–H12 validado.

## Contrato propuesto

- Crear un resultado separado `PROVISIONAL_COLD_START`, visible únicamente para productos con tres a cinco meses consecutivos de venta y datos de identidad, scope, periodo y cantidad íntegros. No cambiar la regla de seis meses del motor validado.
- Los candidatos son (1) baseline local de nivel reciente y (2) trayectoria normalizada de productos comparables de la **misma cadena y categoría**. Un comparable debe tener al menos 18 meses observados y no puede ser el propio producto objetivo ni otro scope padre/hijo sumado. La selección de comparables y el escalamiento usan exclusivamente información disponible al corte simulado.
- Evaluar candidatos en cohortes históricas de otros productos, simulando exactamente la misma edad de arranque (tres, cuatro y cinco meses). Usar rolling-origin/leave-one-product-out y reportar WAPE, Bias y cobertura por H1, H3, H6 y H12 **de los comparables**, con conteos. No atribuir estas métricas a Oxford.
- Elegir una trayectoria agrupada sólo si supera al baseline local en los orígenes y horizontes comparables y no deteriora el Bias según una política versionada. Si no hay al menos tres comparables evaluables, o la prueba no supera la regla de no degradación, mantener `INSUFFICIENT`; no generar doce cifras sin respaldo.
- Guardar en el preview provisional: versión de política/modelo, corte, hash del dataset, IDs de comparables, meses observados del producto, orígenes/targets evaluados, métricas de comparables, motivo de elección y H1–H12. Etiquetar cada valor como provisional y sin certificación point-in-time cuando `available_at` histórico es desconocido.
- No crear ni promover Champion, vintage oficial, decisión de pedido ni publicación automática a partir de este resultado. La UI debe distinguirlo de `Fcst Towell` validado y permitir auditar sus insumos; no mostrar WAPE del producto objetivo cuando no existe.

## Gate de implementación

1. Pruebas de fuga temporal, identidad de cadena/categoría, ceros prearranque, meses faltantes y ausencia de comparables.
2. Backtest de cohortes reales por cadena con comparación contra baseline y métricas por horizonte; reportar también fallos, no sólo ganadores.
3. Pruebas de autorización y de no escritura histórica/oficial.
4. Verificación en Railway con usuario autenticado y un preview real; revisar Oxford sólo después del deploy del backend vigente.

Este documento es una especificación de siguiente etapa. **No habilita ni ejecuta el modelo provisional.**
