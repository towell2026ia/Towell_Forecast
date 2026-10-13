# PRD 09.2E4-OPS-01

Base auditada: `849a71df253e091e984850bb1c69a271cc80bba9`.
La puesta en operación se valida por etapas; las pruebas aisladas no son aceptación productiva.

## Correspondencia

| Requisito | Código existente | Corrección necesaria |
| --- | --- | --- |
| C1 a Cn por cadena, producto y Venta | `e4_api.py`, RPC `e4_create_calculation` | Separar la clave de cada acción de la identidad del preview; conservarla al reintentar una acción fallida. |
| Activar Historial antes de Selección | `E4Operations`, `current-selection` | Tratar `E4_DISABLED` de Selección como ausencia de decisión sin ocultar Historial. |
| Tres alternativas y 12 horizontes | `snapshot_from_preview`, `forecast_calculation_horizons` | Reutilizar el contrato existente y sus métricas de producto. No fabricar candidatos. |
| C1 desde el preview vigente | `preview-latest`, `createCalculation` | Consultar el preview existente para C1 antes de crear otro job. |
| Creación real y corte retrospectivo separados | `created_at`, `issue_period`, snapshots | Mostrar ambos valores en el detalle. No modificar fechas de creación. |
| Decisión humana y motivo de desviación | `validate_selection`, `e4_select_forecast` | Sin cambio; preservar eventos y curva completa. |
| C2 pendiente conserva C1 vigente | `current_forecast_selection`, RPC transaccional | Sin cambio de lógica de decisión. |
| Captura y correcciones versionadas | `e4_save_capture`, `e4_confirm_capture` | Sin cambio; no introducir datos sintéticos en producción. |
| Venta confirmada visible antes de LIVE | Detalle de cálculo, gráfica y comparación | Leer sesiones confirmadas y sus observaciones; mostrar también un punto aislado. |
| No convertir retrospectiva en LIVE | `e4_close_live`, lineage guards | Migración 014: exigir emisión anterior al mes objetivo y a la primera disponibilidad real, y mes objetivo terminado. |
| WAPE y Fill Rate por sumas | `live_metrics`, snapshots SQL | Reutilizar fórmulas existentes y conservar horizonte original. |
| Aprendizaje sin modificar cálculos ni Champion | `forecast_learning_events`, API de lectura | Sin entrenamiento ni promoción automática. Nuevas observaciones alimentan futuros cálculos. |

## Activación

1. Confirmar 011, 012, 013, hardening y fingerprint del histórico certificado.
2. Desplegar el código y aplicar 014 mediante una transacción de esquema. No modificar hechos importados.
3. Confirmar `SUPABASE_SERVICE_ROLE_KEY` únicamente en Railway y la alineación de SHA.
4. Activar v2 en un lote con sus umbrales: 18 meses, continuidad .85/.95, mejora .25, deterioro de Bias 5 y crítico 2; Fill Rate 95.
5. Obtener respuesta autenticada real de quality-gates con `E3-GATES-2.0.0`. Si falla, restaurar v1 y detener.
6. Activar solo Calculation History; verificar salud, permisos, RLS y datos antes de crear exactamente C1 de FENDI AZUL.
7. Verificar C1, sus 12 horizontes y candidatos, usuario, fecha real, motor y SHA. Activar Selección y dejar la elección al usuario.
8. Tras validar la selección, activar Captura. No crear C2 ni capturas para aprobar pruebas productivas.
9. Activar LIVE solo después de verificar una captura real autorizada. Un mes capturado sin cálculo temporalmente elegible queda como comparación, no como cierre LIVE.

Champion, Vintage y publicación oficial permanecen apagados.

## Contrato De Preview E4

El contrato `operational-preview-4-e4-snapshot` separa la identidad de cache de los previews E1.
Los tres campos de procedencia del Ensemble se incorporaron al motor sin cambiar la version E1;
por eso un objeto anterior puede seguir siendo READY_PREVIEW, pero no ser apto para guardar C1.
No completar `statistical_weight`, `ensemble_ml_model` ni `ml_component` a partir de resultados o lideres de scope.
El motor actual emite esos campos al calcular el candidato individual. Las bandas conservan sus conteos originales.
Los objetos y jobs E1 permanecen legibles e intactos. La version nueva genera otra clave solo ante una solicitud explicita.
La reparacion autorizada permite un unico preview de producto FENDI AZUL, corte 2026-07, no un preview de scope ni C1.
Antes de C1 verificar el nuevo resultado, sus campos de procedencia, hash y 12 horizontes; mantener las otras flags apagadas.

## Reversión

Si una etapa falla, apagar su flag y detener el avance. La validación fallida de política v2 exige volver a v1.
No borrar cálculos, decisiones, capturas ni evaluaciones. Una reversión funcional conserva la auditoría.

Los datos sintéticos y fechas anteriores usadas por las pruebas de temporalidad existen únicamente en PGlite o fixtures de test.
