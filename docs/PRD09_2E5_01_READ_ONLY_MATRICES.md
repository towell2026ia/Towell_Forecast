# PRD 09.2E5-01: matrices operativas de consulta

## Alcance

Periodos se reemplaza por la matriz consolidada de los doce objetivos del
calculo almacenado. Historico se convierte en la matriz comercial, con una
vista adicional de horizontes sin observaciones reales. Base de Datos sustituye
a Auditoria en la navegacion. La auditoria tecnica y sus tablas se conservan.
No hay migraciones ni nuevas rutas productivas de escritura.

## Datos y trazabilidad

- Lecturas de Supabase con la sesion del usuario, RLS y filtros de catalogo.
- Calculation ID, corte, objetivo, creacion real, modelo y version conservados.
- El corte de C1 en julio de 2026 produce agosto de 2026 a julio de 2027.
- Mes transcurrido/en curso/futuro separado de emision retrospectiva/anterior.
- Scopes padre excluidos del consolidado por `parent_uuid` autorizado, nunca
  por semejanza entre nombres. Una consulta exclusiva de un padre conserva su
  propio grano y no suma los hijos.
- Totales solo sobre cantidades disponibles, con cobertura y total parcial.
  Cero observado se conserva; falta de dato permanece nula.
- Towell adoptado exige un evento persistido con doce puntos que coincidan
  con el candidato del calculo. Cold Start nunca se convierte en adopcion.
- WAPE retrospectivo conserva sus etiquetas y evidencia del calculo.
- WAPE LIVE requiere una evaluacion persistida y vinculada al mismo calculo,
  objetivo, candidato y version real. Creacion anterior al mes objetivo y a la
  disponibilidad real, madurez del mes y correcciones vigentes se verifican.
  Sin evidencia temporal no se reconstruye LIVE a partir de retrospectivos.
- WAPE acumulado y Fill Rate son cocientes de sumas sobre pares completos;
  no se promedian porcentajes ni se imputan faltantes.
- Historico comercial se pagina por tuplas completas. Los resumenes acumulan
  paginas y no precargan los hechos completos. Base de Datos pagina las
  observaciones en Supabase y conserva las versiones comerciales originales.

## Servicios y seguridad

`GET /api/forecast/provisional-forecasts` devuelve exclusivamente propuestas
numericas previamente almacenadas. Requiere JWT verificado y acceso al scope,
valida los doce horizontes y permite paginas acotadas. No ejecuta modelos,
investigacion, capturas, adopciones ni escrituras del cache.

Base de Datos incluye informacion historica, observaciones comerciales,
catalogo de productos y cadenas, calculos, resultados de modelos, decisiones,
investigaciones conservadas y desempeno. No permite SQL, edicion, eliminacion
ni credenciales privilegiadas. Se retiro el polling automatico de investigacion
al mostrar un preview provisional.

## Restricciones y reversion

Calculation History y Selection conservan sus controles actuales. Capture,
LIVE, Champion, Vintage y Official Publication permanecen desactivados.
La implementacion y validacion no crean C2, no adoptan candidatos y no cambian
C1, observaciones, perfiles ni decisiones. No se ejecuta Deep Research.

La reversion es de codigo/despliegue, sin DML ni migracion inversa. No se deben
modificar las flags de seguridad para revertir estas vistas. Cualquier primera
adopcion requiere autorizacion expresa del operador por separado.

## Verificacion

`tests/portal/reporting.test.tsx` cubre las reglas de matriz, cobertura, fechas,
relaciones, faltantes, LIVE, controles de consulta, paginacion y fallos cerrados.
Los tests de Operational Preview cubren el GET sin escrituras, autenticacion,
scope, limites y ausencia de investigacion. La regresion mantiene los contratos
de autenticacion, captura, Selection, inmutabilidad, RLS y permisos de RPC.

Las evidencias de ejecucion, hashes de integridad, despliegue y QA local se
guardan fuera del bundle y del repositorio publico, bajo
`outputs/prd09_2e5_01/`. Las capturas locales usan la lectura conservada de C1,
Chromium aislado y bloqueo de redes externas; no sustituyen la aceptacion visual
en una sesion autenticada del portal productivo.
