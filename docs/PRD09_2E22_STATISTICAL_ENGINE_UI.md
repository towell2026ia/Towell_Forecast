# PRD 09.2E.2.2 — Motor Estadístico multi-cadena

Base SHA: `33d437724b005ccecd1e569df2399464850def41`. Branch: `main`.

## Arquitectura y límites

El portal productivo importa `operational-engines-view`, no las vistas legacy. Se añaden Resumen, Motor Estadístico y un placeholder de Machine Learning (E2.3 pendiente), sin crear un módulo de sidebar. Resumen conserva los componentes y contratos E2.1.

El detalle es controlado por `PreviewScope`, filtros globales e histórico autorizado. `lib/statistical-preview-data.ts` contiene selección, filtrado, ranking, distribución y validación; no implementa modelos ni backtests. Los nombres del payload Python se conservan en TypeScript.

Fuentes: los GET existentes de preview-latest / preview result y el lector histórico Supabase del usuario. Sólo el botón operacional existente puede solicitar una corrida. Pestañas, foco, visibilidad, categoría, búsqueda y periodo no ejecutan modelos. El componente padre permanece montado al cambiar de módulo; conserva la pestaña y el toggle de comparación durante la sesión. Logout desmonta el portal.

La inspección del código `OperationalPreviewService._result` y del engine confirma candidatos estadísticos por producto, validation_wape/bias, observations y validation_by_horizon, así como statistical_model/classification/forecast_status en los horizontes. No se requiere modificar backend.

| Límite | Resultado de esta entrega |
|---|---|
| Dependencias demo / hardcodes del piloto en ruta estadística productiva | 0, auditado por test S48/S49 |
| Backend / modelos / políticas / parámetros modificados | 0 |
| Supabase migrations / SQL remoto | 0 |
| Variables Railway / cutover / deployment manual Railway | 0 |
| Escrituras históricas / forecast Supabase / Champion / vintages oficiales | 0 |
| Corridas reales disparadas por esta tarea | 0; sólo pruebas locales aisladas |
| Auth, recuperación, permisos, Lottie | Sin modificaciones |

Las vistas y fixtures legacy se conservan para regresión; no se reutilizan como fuente productiva.

## Semántica visible

- Producto: H1 determina modelo; exactamente H1–H12 deben coincidir en modelo, fechas y valores finitos. Una inconsistencia produce aviso y suprime forecast parcial. Ranking también advierte el caso.
- WAPE/Bias de producto se toman exclusivamente del candidato estadístico de ese producto/modelo. Observaciones son observaciones de validación, no meses históricos.
- Scope: métricas retrospective_wape/bias del motor, distribución literal `statistical.models`, catálogo literal `available_candidates`, elegibilidad y cobertura stat_eligible/evaluated. Categoría/búsqueda filtran ranking, no reconstruyen distribución ni métricas del scope.
- Candidatos dinámicos, sin máximo de filas: orden WAPE ascendente, Bias absoluto ascendente, nombre con comparación de strings equivalente a Python. Métricas ausentes/no finitas van al final y se muestran como `—`, nunca 0, NaN% o Sin evidencia%.
- La explicación sólo afirma menor WAPE/desempate cuando lo demuestra la lista entregada por E2. No se inventan razones de tendencia/estacionalidad.
- Intermitencia requiere modelo Croston/SBA/TSB **y** clasificación literal Intermitente. No se infiere otra clasificación.
- Gráfica: Venta histórica hasta issue_period, forecast estadístico futuro principal, sin conectar huecos. Comparación Towell opcional, desactivada inicialmente. No hay líneas alternativas ni backtests reconstruidos.
- WAPE por horizonte: sólo entradas literales, barras sólo no-null; la tabla conserva `—` para entradas null. Una entrada ausente permanece ausente; metadata duplicada/inválida falla cerrada.
- Tabla futura: doce filas literales, modelo, periodo y statistical_value. Productos no elegibles muestran estado real y explicación, no forecast ficticio.
- Todas las cadenas: una fila independiente por scope; ninguna gráfica/suma estadística global, sin double counting parent/child. CUTS_NOT_ALIGNED se conserva.
- Responsive en código: KPIs 1/2/3/5 columnas, gráfico 300/400px, comparación apilada/dos columnas y tablas con scroll horizontal. Pestañas con roles ARIA y navegación por flechas/Home/End.

## Certificación automatizada

`tests/portal/statistical.test.tsx` agrega 38 casos ejecutables; los IDs S01–S38 y S48–S49 están mapeados a los tests. S39–S47 se cubren por las suites trader, preview, portal, period, auth, session, context y assistant existentes, además del test de navegación nuevo.

| Controles | Evidencia |
|---|---|
| S01–S07 contrato | Fixtures tipadas y compilación TypeScript |
| S08–S14 producto | Filtro family/product, H1, doce horizontes consistentes, métricas producto distintas de scope, nulos |
| S15–S19 comparación | Desempate, badge Seleccionado, 14 modelos y modelo futuro, observaciones, sin Champion |
| S20–S24 horizonte | H1/H12 WAPE, null != zero, no backfill, doce futuros literales |
| S25–S29 scope | Distribución, más seleccionado, denominador evaluated, WAPE/Bias scope |
| S30–S34 estados | ACTIVE, COLD_START, INACTIVE, PRE_LAUNCH, INSUFFICIENT sin forecast falso |
| S35–S38 todas | Filas/porcentajes independientes, sin suma global, cortes independientes |
| S39–S47 regresión | Resumen, Trader/Towell, comparación, horizontes/filtros/histórico, Auth/Lottie, foco/visibilidad y round-trip de módulo |
| S48–S49 aislamiento | Audit de fuentes productivas sin imports demo ni hardcodes del piloto |

Validación local: portal 200 tests PASS (16 archivos), backend 487 tests OK con 7 skips preexistentes (480 PASS), motores 110 PASS, audit engine PASS, seguridad SQL local 36/36 y RLS 28/28, baseline SQL local PASS, lint sin errores (4 warnings de dependencia .venv), typecheck y builds Netlify/Sites PASS. Scanners browser bundle sin configuración privilegiada. Docker no está disponible localmente; su build y smoke con volumen deben certificarse por CI, no asumirse.

Los escenarios 45 evaluados, 20 elegibles y 14 modelos de tests son fixtures sintéticas de presentación. **No son una verificación independiente del preview real de Al Super.** El corpus previamente certificado (39,270 observaciones / 18 scopes / 1,010 productos) no fue alterado ni recontado en una sesión productiva autenticada por esta entrega.

## Runtime y aceptación pendiente

Comprobación read-only de Railway: production, data=supabase, persistence=sqlite, auth=SupabaseAuthProvider, operational_preview=true, official_publication=false. `/api/version` reporta `5f864917498f8f4366212c4bd89d565e1961fe09`; es compatible con una entrega frontend-only.

La revisión con `computer-use` no pudo iniciarse: kernel del navegador falló con `windows sandbox failed: helper_unknown_error: setup refresh had errors`, también tras reset y un reintento. No se obtuvieron screenshots ni métricas/product IDs de una sesión real. No se extrajeron credenciales ni se automatizó login.

Una vez Netlify publique el commit, el propietario debe revisar:

1. Al Super / Todos: números reales de evaluados/elegibles, cobertura, catálogo, distribución y métricas scope.
2. Producto elegible: ID/clasificación/modelo reales, WAPE/Bias/observaciones, gráfica, WAPE por horizonte y H1–H12.
3. Intermitente si existe; no elegible con estado/explicación; Todas con cobertura independiente.
4. Resumen E2.1 intacto, toggle Towell inicialmente OFF y pestaña/toggle/filtros conservados al cambiar de módulo/ventana.
5. Desktop/tablet/mobile, tooltips y scroll real de tablas.

No es necesario pulsar Calcular ni compartir contraseñas/tokens. Hasta obtener esa aceptación, el estado final es `BLOCKED_MULTICHAIN_STATISTICAL_ENGINE_UI` por validación visual/productiva pendiente, no por un fallo de los tests. Registrar commit, CI y Netlify SHA en la entrega final con resultados comprobados.
