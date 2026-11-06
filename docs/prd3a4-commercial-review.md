# PRD 3A.4 — revisión de identidades comerciales

La conciliación privada de 3A.3 es la fuente de las 427 identidades. La preparación
de 3A.4 no crea identidades ni cambia productos de Supabase. En el snapshot revisado,
378 identidades tienen un único candidato por UPC: 356 sin alerta de formato y 22
con una discrepancia de cadena o formato. Otras 49 carecen de UPC exacto. Ningún
candidato queda aprobado automáticamente.

## Matriz privada

`scripts/prepare_commercial_review.py` toma la conciliación, el snapshot de
catálogo de 3A.3 y la lista vigente de productos. Produce un borrador de filas,
dos reportes de excepciones y un resumen con hashes SHA-256 de las entradas.
Los archivos generados se guardan en `datos_privados/salidas`, fuera de Git.

La hoja `Revision_427` mantiene una fila por identidad. Las columnas A:Q son
evidencia de origen y candidato. Las columnas R:X son editables para dictamen,
producto y cadena aprobados, responsable, fecha, evidencia y observaciones.
El dictamen inicial de todas las filas es `Pendiente`. La hoja `Guia_y_fuentes`
explica los grupos y las variables. La similitud de descripción sólo es una
pista de investigación, nunca una aprobación de identidad.

## Validación local

`scripts/validate_commercial_decisions.py` recibe el XLSX editado, el JSON
`matriz_entrada.json`, el JSON de productos vigentes de 3A.3 y una ruta de salida
que aún no exista. Su salida es exclusivamente un reporte JSON local: no realiza
llamadas de red ni escrituras de base de datos. Ejemplo:

```powershell
python scripts/validate_commercial_decisions.py `
  --xlsx <matriz_editada.xlsx> `
  --baseline <matriz_entrada.json> `
  --current <productos_y_evidencia.json> `
  --output <validacion_nueva.json>
```

El validador exige exactamente 427 filas, el orden y los campos de origen
intactos, un dictamen válido y, para aprobar o rechazar, responsable, fecha y
evidencia. Una aprobación requiere un producto activo existente, evita asignar
el mismo objetivo a dos identidades y exige un identificador de evidencia
específica en estos casos:

| Caso | Marca requerida en evidencia |
| --- | --- |
| Sin UPC exacto | `UPC_ALTERNATIVO_VERIFICADO:` |
| Diferencia de cadena/formato | `SCOPE_VERIFICADO:` |
| Cambio de producto candidato | `CAMBIO_PRODUCTO_VERIFICADO:` |

Estas marcas obligan a documentar la resolución, pero por sí solas no prueban
que la evidencia sea auténtica. Un rechazo necesita también un motivo en
observaciones. Si hay errores, el reporte queda `BLOCKED` y no devuelve
aprobaciones utilizables. Incluso cuando no hay errores, el resultado es
`READY_FOR_COMMERCIAL_REVIEW`, con `production_load: false`: requiere revisión
humana antes de cualquier integración posterior.

## Fuentes y límite de integración

La hoja privada Towell es forecast interno histórico, Cliente es forecast
recibido y Pedidos es pedido real. Venta y Entrega proceden de fuentes
independientes de la plataforma, con `metric_code` `SALES` y `DELIVERY` en
`monthly_observations`; su correspondencia con las identidades privadas sigue
sin acreditar. El inventario en punto de venta no tiene aún fuente acreditada.
Para esas tres integraciones se muestra `Sin dato` hasta disponer de evidencia.
No se certifica WAPE LIVE por estos datos retrospectivos.

Esta fase no aplica SQL, no importa decisiones a producción y no modifica
V01–V03, cálculos, motores, gráficos, formularios ni Champions.
