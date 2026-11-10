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

El cálculo numérico provisional se activa en el backend operacional para productos seleccionados con comparables válidos. Se ejecuta mediante la acción explícita del usuario autorizado. La vista previa general carece de la selección completa de formato, modelo y tipo de compra requerida por la investigación RS.

La integración RS reutiliza `ColdStartResearch`, Responses API y el coordinador existente. `OPENAI_API_KEY` se conserva únicamente como secreto del backend. `OPENAI_ENABLED` y `DEEP_RESEARCH_ENABLED` deben habilitarse juntos y requieren una clave configurada. `AI_ASSISTANT_API_ENABLED` habilita la nueva ruta operativa autenticada. La publicación de código y los cambios de configuración siguen sujetos a la autorización de despliegue. Los métodos estadísticos y los flags de publicación conservan su contrato.

## Asistente RS y procedencia

La ruta `POST /api/forecast/rs-assistant` recibe cadena, tipo RS, formato, modelo y periodo. El mismo JWT autoriza todas sus lecturas. Exige que la versión activa sea la derivada del manifiesto `72f4b1a25d7d26823c28d10f39326b2743d3eb2655ad009f017b0257a4dec20d`. Si B está inactiva o falta el contrato versionado, devuelve un error y no consulta los snapshots del piloto.

Los datos de Venta, Pedido, Entrega y forecasts históricos se mantienen separados. Para atribuir cantidades a RS, se requiere procedencia del archivo BAASE. Los valores heredados sin esa acreditación se excluyen e informan en cobertura. El inventario es agregado por cadena, formato, producto y mes. No representa existencias por tienda.

El usuario puede solicitar una comparación provisional con el motor existente. El coordinador comprueba primero la historia mínima, conserva los métodos existentes, calcula sólo el alcance acreditado y reutiliza el resultado de la misma selección y datos. El resultado no publica un Champion. WAPE y Bias se identifican como retrospectivos y LIVE continúa sin certificar cuando falta evidencia temporal.

## Investigación externa y presupuesto

Los roles con permiso de ejecución para la cadena pueden iniciar investigaciones mediante una solicitud explícita, un cálculo RS elegible o una pregunta sobre mercado, estacionalidad, tendencias o riesgos comerciales. Se aplica la autorización operativa continua de uso de la clave existente de Railway. Los lectores pueden consultar resultados del alcance autorizado. La navegación y las preguntas sobre métricas internas por sí solas no inician búsquedas. La caché persistente se distingue por cadena, formato, modelo, corte, versión operativa y política. Las consultas externas reciben únicamente un tema genérico de producto procedente de una lista controlada y un mes validado. Nunca reciben cantidades, identificadores internos, textos libres del catálogo, mensajes del usuario ni archivos.

Se conserva el modelo existente `gpt-5.5`, con tres llamadas máximas de herramientas y 3,000 tokens de salida por respuesta. El control de concurrencia admite cuatro investigaciones en curso por almacenamiento compartido. Una solicitud nueva puede volver a intentarse cuando se libera un lugar. El sondeo de una investigación ya iniciada tiene intervalo y vencimiento; los fallos de proveedor se registran sin reintentos automáticos. El resultado registra uso, fechas reales de consulta y motivos de error sin divulgar credenciales ni IDs del proveedor.

Los hallazgos son contexto público de categoría. Una cita URL no acredita por sí sola la fecha de publicación o la accesibilidad de la fuente. Esos controles se verifican en la prueba real antes de declarar operativa la función. Las cantidades Python permanecen separadas de la investigación.

Con los precios consultados, un ejemplo de 10,000 tokens de entrada, 3,000 de salida y tres búsquedas cuesta aproximadamente USD 0.17. La entrada es una hipótesis de cálculo, no un límite garantizado. Debe reportarse el uso real del proveedor y confirmar la facturación del proyecto. [Modelo GPT-5.5](https://developers.openai.com/api/docs/models/gpt-5.5) · [Precios de herramientas](https://developers.openai.com/api/docs/pricing)

## Gate de verificación

1. Pruebas de fuga temporal, identidad de cadena/categoría, ceros prearranque, meses faltantes y ausencia de comparables.
2. Backtest de cohortes reales por cadena con comparación contra baseline y métricas por horizonte; reportar también fallos, no sólo ganadores. El backtest sintético no reemplaza esta aceptación productiva.
3. Pruebas de autorización y de no escritura histórica/oficial.
4. Verificación en Railway con usuario autenticado y un preview real; revisar Oxford sólo después del deploy del backend vigente.

La aceptación productiva de Oxford requiere verificar el nuevo SHA de Railway y una corrida autenticada que muestre los doce horizontes y el número de comparables. Hasta esa prueba, no atribuir un PASS de Oxford real.
