# Consulta de motores almacenados

Motores reutiliza el mismo `ReportSnapshot` fijado a la fuente oficial que
Períodos y el exportador. La consulta incluye una variante o una selección
comercial de varias variantes, aunque no exista una vista previa reciente.
No ejecuta Python, importa observaciones ni crea cálculos.

`storedCommercialScope` selecciona los cálculos compatibles por hash de fuente,
generación, objetivo Venta, corte, versión de motor y modo de evidencia. Cada
candidato debe pertenecer al producto y coincidir con sus doce horizontes
almacenados. Una vista previa de otro alcance no completa métricas ausentes.

Las métricas de `candidate_metrics` corresponden a los candidatos principales
guardados de Estadístico, ML y Ensemble. No representan el catálogo completo
de métodos internos. Se conservan WAPE, Bias, errores, observaciones, ventanas,
firma y resultados por horizonte disponibles. Un valor cero sigue siendo cero;
un dato no persistido sigue ausente. Las muestras de entrenamiento no guardadas
no se muestran como cero.

Las curvas del modelo suman sus variantes y exponen cobertura por familia.
Sin pares de errores y denominadores comparables no se calcula WAPE o Bias
consolidado, ni se promedian porcentajes de productos. Tampoco se reutiliza el
líder de una cadena como recomendación de un modelo filtrado.

La recomendación técnica es la sugerencia congelada del cálculo, con sus
razones originales. No se vuelve a elegir el menor WAPE en el navegador.
FT-Decision corresponde exclusivamente a eventos con una curva validada contra
el cálculo. Sus fechas, actor, motivo e historial se conservan separados de
Champion. Una recomendación o decisión no publica ni certifica un Champion.
Los cálculos consultados no aportan un certificado de publicación; la interfaz
lo declara expresamente.

La fecha de registro SQL y la emisión acreditada por la procedencia offline se
presentan por separado. La ausencia de esta última no se rellena con la primera.

En el gateway local de sólo lectura, los botones y los handlers de cálculo,
adopción, captura y publicación están bloqueados, además de mantener las
validaciones de rol y flags. No existe un guardado simulado ni una redirección
de escrituras al servicio hospedado. Registrar una decisión local requiere
habilitar posteriormente un gateway de escritura autorizado; esta corrección
no lo implementa.

Las pruebas sintéticas ejercitan datos sin preview, linaje incompatible,
recomendaciones conservadas, ML parcial, métricas nulas, decisiones y ausencia
de publicación. La comprobación comercial privada contrasta los mismos helpers
de Motores, Períodos y XLSX sin guardar archivos comerciales en Git.
