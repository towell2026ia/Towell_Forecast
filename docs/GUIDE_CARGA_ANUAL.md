# FORECAST Towell — carga anual comercial

PRD 09.2E5-02A.0 · Contrato local v1.0.0 · 2026-10-18.

La entrega prepara información para una conciliación futura. Incluye Excel vacío,
generador, esquema JSON y validador local. El validador no consulta Supabase,
no importa información y no ejecuta motores. No requiere credenciales.

## Uso por personal comercial

1. Copie `templates/FORECAST_Towell_Plantilla_Carga_Anual.xlsx`. Use un archivo por
   cadena y año, por ejemplo `CODIGO_CADENA_2024_v1.xlsx`. Incluya todos los modelos
   y productos de esa cadena. No cree un archivo por modelo.
2. Lea `00_INSTRUCCIONES`. Conserve las ocho hojas y los encabezados exactos.
3. Registre las fuentes en `05_FUENTES`. Asigne claves documentales locales que
   pueda reconocer; no son UUID de Supabase ni hashes.
4. Complete una sola fila en `01_CONTROL_CARGA`. Declare el código verificado de
   cadena y el año del histórico, entero desde 2024. El código admite mayúsculas,
   números, guion y guion bajo. No se comprueba offline su existencia en Supabase.
5. Complete `02_PRODUCTOS`, una fila por código de origen y variante. Declare
   sólo un UUID de producto cuya correspondencia ya haya sido verificada.
6. Capture en `03_HISTORICO_MENSUAL` únicamente los meses con observaciones reales.
   No complete doce meses automáticamente. Cada fila necesita al menos una
   cantidad: venta real, pedido o enviado.
7. Si dispone de Forecast Cliente, complete `04_FORECAST_CLIENTE`. La versión es
   un entero positivo. Conserve cada versión y distinga emisión, objetivo y
   recepción. Los objetivos pueden superar el año declarado del histórico.
8. Consulte `06_DICCIONARIOS`. No deduzca equivalencias por semejanza del nombre.
9. Guarde y cierre el archivo. Ejecute el validador. Revise los errores y las
   advertencias antes de entregar el archivo para una conciliación posterior.

Encabezados azul oscuro y primera fila ámbar identifican campos obligatorios
cuando existe una fila; los opcionales tienen encabezado gris azulado. Los
campos incompletos de clasificación producen advertencias, sin eliminar productos.
Las hojas de captura permanecen editables. Las tablas tienen filtros y filas
congeladas; no hay columnas ocultas. Pegue dentro de la tabla o extiéndala con Tab
desde la última celda. Python también lee registros fuera de la tabla.

Los avisos de Excel ayudan durante la captura; pegar datos puede eludirlos.
La revisión completa siempre requiere Python. No modifique cantidades para
evitar un error: revise el documento fuente.

## Formatos y significado

- **Vacío**: dato no proporcionado. **0**: cantidad real confirmada como cero.
- **Texto**: códigos, ITEM, UPC, variantes, modelos y periodos. Prepare la celda
  como texto antes de pegar. `000123` debe conservar sus ceros iniciales. Cambiar
  el formato de un número después no recupera ceros perdidos.
- **Periodo**: texto `YYYY-MM`, por ejemplo `2024-01`. El histórico debe pertenecer
  al año de control. No usar nombres de mes, fechas completas ni `2024-1`.
- **Fecha**: fecha Excel sin hora, mostrada `yyyy-mm-dd`. Python también acepta
  texto ISO válido `YYYY-MM-DD`. No invente fechas desconocidas. Una fecha de
  corte documental no prueba cuándo estuvo disponible un dato.
- **Cantidad**: celda numérica finita no negativa, hasta tres decimales. Se valida
  el límite de `numeric(18,3)` sin redondeo. Excel conserva aproximadamente quince
  dígitos significativos: cantidades de mayor precisión requieren cotejo con la
  fuente. No pegar cantidades como texto, fórmulas ni errores de Excel.
- **Descripción original**: literal del documento; no reemplazar por el modelo
  homologado. La descripción no es identidad suficiente para fusionar productos.

La clave del catálogo es `codigo_producto_origen + variante_codigo`. La variante
vacía representa ausencia de variante; no se genera una. La clave histórica añade
`periodo`. Ambas comparan textos exactos: no se cambian ceros, mayúsculas, espacios
ni signos de identificadores. Espacios exteriores en claves se reportan como error.
ITEM y UPC no se convierten automáticamente en `product_code`.

## Homologación y clasificación

Los modelos, colores, tipos y presentaciones son abiertos: no hay listas cerradas
de modelos. FENDI OXFORD, un modelo nuevo y nombres de varias palabras son válidos.
La clave informativa de modelo aplica Unicode NFC, mayúsculas y compactación de
espacios; ` Fendi  Oxford ` propone `FENDI OXFORD`. No elimina palabras, acentos,
signos o calificadores, ni aplica similitud textual. No fusiona identidades.

Sólo se precargan estas reglas comerciales confirmadas por este PRD:

| Atributo | Original | Homologado |
| --- | --- | --- |
| Presentación | MB / MEDIO BAÑO | MEDIO BAÑO |
| Presentación | BANO / BAÑO | BAÑO |
| Presentación | PULL / PULLMAN | PULLMAN |
| Presentación | FACIAL | FACIAL |
| Tipo de producto | BATA / BATAS | BATA |

BATA o BATAS en tamaño es un error. Otras presentaciones se conservan sin inventar
equivalencias. Las reglas agregadas por el usuario se revisan y nunca modifican
las celdas originales. Reglas PENDIENTE no se aplican. Reglas confirmadas
contradictorias producen ERROR. Etiquetas como SURTIDO, POR DEFINIR o POR CONFIRMAR
generan advertencia; otros casos ambiguos se registran como equivalencia PENDIENTE.
La validación no pretende resolver ambigüedades semánticas automáticamente.

`estado_clasificacion` conserva los estados del contrato actual: PROPOSED,
APPROVED, PENDING_CLASSIFICATION y REJECTED. Vacío, estado provisional o atributos
incompletos mantienen el producto pendiente y visible. APPROVED declarado también
genera advertencia: su autorización y evidencia no se verifican offline.
Homologación y estado de equivalencia no constituyen aprobación de clasificación.

El SQL separa estos estados de `model_state`, `color_state` y `size_state`
(KNOWN/UNKNOWN y NOT_APPLICABLE donde corresponda). La plantilla no añade columnas
para estos estados ni interpreta un vacío como NOT_APPLICABLE: documente la
justificación en observaciones para su futura conciliación.

## Ejecución local

Requiere Python 3.12 o posterior y openpyxl 3.1.5. Desde la raíz del repositorio
o del paquete entregado:

```sh
python -m pip install -r requirements-annual-template.txt
python scripts/generate_annual_template.py --output work/plantilla_nueva.xlsx
python scripts/validate_annual_template.py "ruta/CADENA_2024_v1.xlsx" --output-dir work/validacion --report-workbook work/consulta_validada.xlsx
```

Para calcular los hashes de archivos fuente reales, agregue
`--source-dir "ruta/fuentes"`. Los nombres de `05_FUENTES.nombre_archivo` se
resuelven dentro de ese directorio, sin permitir escapar fuera de él. No hay red.
Sin esa opción se revisan las referencias documentales, pero no se confirma la
existencia del archivo fuente. No escriba hashes manualmente. No cambie el original
mientras el validador lo está leyendo. Se registra su SHA-256 antes y después.

Salidas locales:

| Archivo | Contenido |
| --- | --- |
| `validation_summary.json` | Conteos, errores, duplicados, faltantes, diferencias y estado |
| `validation_errors.csv` | Detalle por hoja/fila/campo, severidad y propuesta informativa |
| `validation_status.json` | Estado, huella del original, integridad y hashes de fuentes si se pidieron |

La consola imprime sólo un objeto JSON de resumen; no imprime todas las filas.
El CSV neutraliza texto que Excel podría interpretar como fórmula. Las salidas
pueden contener descripciones de errores privadas: consérvelas localmente.

Estados: ERROR si existen incumplimientos; ADVERTENCIA si no hay errores y quedan
aspectos por revisar; VALIDO si no se detectan incidencias locales. Un resultado
local no prueba autorización, identidad remota o aprobación comercial.
Código de salida: 0 para VALIDO/ADVERTENCIA, 1 para ERROR y 2 para uso o destinos
inválidos. Una plantilla oficial vacía **debe producir ERROR de captura** por falta
de cadena y productos; eso no es un defecto estructural del archivo.

### Hoja 07_VALIDACIONES

En el archivo de captura contiene fórmulas de prechequeo de cadena/año y conteos
simples de códigos. Los indicadores que requieren revisar todas las hojas muestran
`Ejecutar validador local`; no se declara un falso cero de errores.

La opción `--report-workbook` genera automáticamente **otra copia de consulta** con
los catorce indicadores completos de Python, incluido estado general, duplicados,
productos pendientes y fuentes faltantes. Mantiene los datos capturados y protege
la copia contra edición accidental. Nunca se sobrescribe el archivo de entrada.
La protección no es cifrado ni sustituye permisos. Vuelva a validar el archivo
de captura después de cualquier corrección; la copia es una fotografía del momento
y muestra la huella del archivo que fue revisado. Un contenedor corrupto, con
macros o conexiones no se reproduce como copia de consulta; revise los JSON/CSV.
Las fórmulas rechazadas se muestran como texto literal en la copia de consulta.

## Revisión del contrato Supabase existente

Se revisaron las migraciones del repositorio y el metadato remoto vigente el
2026-10-18, mediante transacción READ ONLY: columnas, restricciones, RLS y versiones
de migración. Resultado: 15 tablas, 154 columnas, RLS habilitado en las 15 tablas;
migración registrada más reciente `202610140015`. No se leyeron filas comerciales
ni se alteraron datos o entidades. Referencias locales:
`supabase/migrations/202609250001_contract_core.sql` y
`supabase/migrations/202610140015_commercial_catalog.sql`.

| Contrato | Correspondencia y límite |
| --- | --- |
| chains | codigo/nombre para conciliación posterior con code/name; no se inventa chain_id |
| categories | Nombre comercial requiere resolver category_id y code, sin deducir por parecido |
| products | description e identidad verificada; origen no equivale automáticamente a product_code; variante requiere mapping |
| monthly_observations | venta_real→SALES, pedido→ORDER, enviado→DELIVERY; una fila ancha prepara hasta tres observaciones, sólo si hay cantidad |
| commercial_models | modelo original y clave de comparación; UUID y aprobación requieren conciliación |
| commercial_colors | Etiqueta original; colores ambiguos quedan pendientes |
| commercial_sizes | Presentación original y equivalencias confirmadas; no inferir aplicabilidad |
| commercial_product_types | Tipo original; BATA pertenece aquí |
| product_commercial_classifications | Atributos originales y estado declarado; no crea clasificaciones ni aprueba propuestas |
| customer_forecast_versions | Emisión, versión positiva, recepción y estado documental declarado |
| customer_forecast_rows | Producto, periodo objetivo y cantidad; unicidad por emisión/versión/producto/variante/objetivo |
| import_profiles | Perfil posterior por cadena; no se crea desde este Excel |
| import_profile_versions | Mapping explícito, columnas requeridas y revisión de identificadores en etapa futura |
| import_batches | Lote mensual y hash real; el archivo anual exige diseñar la partición posterior |
| source_evidence | Clave fuente local requiere conciliar UUID, archivo real, hash, almacenamiento y nivel de evidencia |

Cada columna de captura tiene su correspondencia o ausencia de equivalencia directa
en `templates/annual_template_schema_v1.json` (`supabase`). El instructivo incluido
en el Excel explica todos los campos. Este contrato es descriptivo; ningún mapping
ejecuta escrituras.

### Pendientes que bloquean un importador productivo futuro

- `anio`, `version_archivo`, ITEM, UPC y varios metadatos documentales no tienen
  columna directa universal en el núcleo. Hay que acordar el mapping/evidencia
  utilizando contratos existentes, sin añadir SQL en este PRD.
- products exige categoría, primeros/últimos periodos y lote de origen. Se deben
  resolver con evidencia, nunca inventar UUID, categoría o periodos ausentes.
- Las cantidades reales se almacenan con periodo fecha al primer día del mes,
  métrica, version_no y reglas de disponibilidad. El corte de fuente no acredita
  available_at: UNKNOWN requiere fecha de disponibilidad nula.
- import_batches tiene un periodo mensual y unicidad de cadena+SHA-256. No se
  puede repetir sin diseño el mismo hash anual en doce lotes mensuales. La
  estrategia de partición y trazabilidad queda pendiente de otro PRD.
- customer_forecast_versions exige received_at con hora/zona y actor autenticado.
  Una fecha del Excel no justifica inventar hora, zona, validación o congelamiento.
  La recepción debe ser consistente por emisión y versión. Los estados FROZEN y
  VALIDATED declarados no certifican sus fechas ni sus actores.
- source_evidence requiere hash real y evidencia; las fechas de disponibilidad y
  cualquier certificación deben estar documentadas y autorizadas.
- La aprobación de clasificaciones necesita evidencias, actores y privilegios.
  No se acredita con una etiqueta APPROVED escrita en Excel.

Estos pendientes se reportan como límites de conciliación futura. No impiden
preparar y validar el archivo local; sí impiden afirmar que una carga productiva
esté implementada. No se cambiaron tablas, índices, funciones, RLS ni migraciones.

## Pruebas y publicación

```sh
python -m unittest discover -s tests/annual_template -p "test_*.py" -v
python scripts/check_annual_template_public.py
python scripts/check-public-repo.py
```

Las pruebas generan datos exclusivamente sintéticos en directorios temporales.
Cubren años 2024–2026, múltiples modelos abiertos, variantes, ceros iniciales,
valores cero/vacíos, fechas, cantidades, duplicados, relaciones, homologaciones,
archivos malformados, copias de consulta y más de doce mil observaciones.
Los ejemplos JSON/CSV en `docs/examples/annual_template/` son sintéticos y no son
archivos listos para carga. La plantilla oficial permanece sin datos comerciales.

La revisión visual se realiza sobre las ocho hojas renderizadas desde el XLSX.
Se comprueban también tablas, formatos, validaciones y estructura OOXML. No se
afirma una prueba interactiva dentro de Microsoft Excel 365 en este entorno.
No hay macros, conexiones externas, credenciales o importador productivo.

Sólo el archivo vacío oficial tiene excepción al bloqueo de Excel en Git: el
control de publicación inspecciona su paquete OOXML y rechaza datos en captura,
diccionarios distintos de las reglas precargadas, macros, partes inesperadas y
conexiones. Los archivos comerciales y reportes privados deben quedar fuera de Git.
CI valida esta entrega en una rama; no necesita cambios funcionales ni despliegues
en Railway o Netlify.
