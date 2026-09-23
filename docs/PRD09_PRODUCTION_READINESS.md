# PRD 09 — Production readiness

Evaluación: 23 de septiembre de 2026. Alcance: robustez, seguridad y despliegue; sin cambios de modelos ni de layout. El PRD queda **implementado parcialmente y no autorizado para producción pública** hasta cerrar las comprobaciones señaladas.

| Dimensión solicitada | Estado | Motivo |
| --- | --- | --- |
| Code Complete | **NO** | Componentes locales, CI y build Docker están verificados, pero falta E2E web autenticado en hosting. |
| Hosting Ready | **NO** | FastAPI standalone y build Docker pasaron; contenedor con volumen persistente y despliegue aún no ejecutados. |
| Auth Ready | **NO** | Backend valida HMAC/roles y frontend firma identidad del hosting, pero falta verificar que el proxy público elimine encabezados falsificados. |
| OpenAI Ready | **NO** | Contrato/placeholder preparado y OFF; no existe cliente API ni credencial seleccionada. Connected: **NO**. |
| Supabase Ready | **NO** | Contratos y export/validate/import/reconcile preparados; adaptadores de red deshabilitados. Connected: **NO**. |
| Voice Ready | **NO** | Contrato deshabilitado, sin implementación. Connected: **NO**. |
| Deployment Ready | **NO** | CI verde; requiere prueba de contenedor/volumen, identidad del hosting y rollback ensayado en staging. |

## Evidencia verificada

- Entornos `development`, `test`, `staging` y configuración `production` validable con `Settings`. Flags remotos en `true` fallan al arrancar; staging/production exigen origen HTTPS, secreto servidor-servidor y allowlist.
- FastAPI standalone respondió 200 en `/api/live`, `/api/health`, `/api/ready`, `/api/version`, WAPE, Champion y forecast 12M. CORS por origen exacto, headers, request ID, códigos seguros, READ/EXECUTE/ADMIN y token firmado se probaron localmente.
- Assistant degradado respondió 503 sin inventar respuesta; forecast continuó 200. Sin datos, readiness devolvió `not_ready`/503. Backup SQLite, restauración a estado anterior e importación/reconciliación local se probaron.
- 43 pruebas del assistant/backend y 96 de motores pasaron localmente (139 en total). Cobertura del paquete operativo `assistant_api`: **81%**, registrada como piso de CI; se excluyeron tests y entradas CLI del denominador. El [CI del commit `cf6f0c4`](https://github.com/towell2026ia/Towell_Forecast/actions/runs/35909365062) pasó en GitHub: escáner, auditoría de dependencias, lint, type-check, tests, build frontend y build Docker.
- Auditoría npm de dependencias de producción: 0 vulnerabilidades conocidas tras actualizar sólo `baseline-browser-mapping` 2.10.30 → 2.11.25 en el lockfile. `pip-audit` para las cuatro dependencias Python fijadas: 0 vulnerabilidades conocidas al momento de la consulta. Ninguna auditoría garantiza ausencia de fallas futuras.
- Vintage `V-RUN-FENDI-2026-08-003` sigue `FROZEN`, E3, `VALID_WITH_DOCUMENTED_LOCAL_EVIDENCE`, 12 horizontes. La revalidación local no alteró su hash. Los 192 registros 2024 continúan `availability_unknown`. El Champion publicado en `app/data` no se modificó.
- No hubo cambios de CSS, Dashboard, Lottie ni gráficos. El archivo de vista estadística sólo corrigió una anotación TypeScript `data_quality` para que type-check pase; la baseline de hash textual se actualizó a ese contenido, sin cambiar JSX/runtime.

## Criterios de aceptación PRD 09

| CP | Estado | Observación |
| --- | --- | --- |
| 01–06 | PASS | Entornos, configuración central y `.env.example`. |
| 07 | PASS | Escáner de archivos rastreados, local y en CI. |
| 08 | PASS build | Docker build en CI; ejecución del contenedor y volumen todavía pendientes. |
| 09–13 | PASS local | FastAPI standalone, rutas portables, AuthProvider y permisos. |
| 14–20 | PASS local | CORS, checks, salud de proveedores, logs, request IDs y errores. |
| 21–24 | PASS local | Rate, concurrencia, timeout de puente web y retry opt-in. Límites por proceso. |
| 25–31 | PASS local / preparados | Backup/restore y proveedores futuros deshabilitados. |
| 32–33 | PASS | Workflows backend y frontend verdes en GitHub. |
| 34–35 | PASS local | Tests Python y build frontend. |
| 36 | PARCIAL | Assistant→FastAPI local probado; frontend→hosting→FastAPI autenticado pendiente. |
| 37 | PASS en fixture | Comando autorizado→research→modelos→Champion→vintage→SQLite con pipeline controlado; corrida real aislada completó 12 horizontes. |
| 38–39 | PASS local | Gate/research/vintage histórico y no-leakage; E3 real revalidado desde caché, no reentrenado. |
| 40–43 | PASS local | XLSX/SQLite excluidos; E3 y bloqueo 2024 conservados. |
| 44–46 | PASS por diff | Sin cambios de layout/Lottie/Champion; falta comparación visual hospedada. |
| 47–50 | PASS documental | Deployment, runbook, rollback e informe. |

## Bloqueadores para cierre definitivo

1. Staging con FastAPI hospedado, volumen SQLite persistente y prueba de backup/restore/rollback real.
2. Verificación del contrato de identidad del hosting: encabezados de usuario autenticado no falsificables desde navegador; allowlist y HTTPS reales.
3. E2E autenticado frontend→FastAPI y comparación visual antes/después en staging; baseline de carga del Dashboard con sesión válida.
4. Política operativa de reinicio de jobs pendientes y coordinación compartida si se usan varios workers/instancias. Mientras tanto, un solo worker.

No activar OpenAI, Supabase, Deep Research ni voz para cerrar estos puntos. Los adaptadores preparados no equivalen a conexiones productivas.
