# PRD 09.1 — Deployment validation gate

Estado: **ABIERTO**. Este informe distingue las pruebas locales/CI de las pruebas que requieren un FastAPI de staging realmente hospedado. No se habilita el asistente público por haber pasado únicamente tests locales.

| Gate | Estado | Evidencia pendiente |
| --- | --- | --- |
| Persistent Storage | PENDIENTE EN HOSTING | Volumen montado, registro técnico y datos E3 consultables tras restart, redeploy y crash. |
| Identity Spoofing | PASS LOCAL / PENDIENTE WEB | Backend verifica HMAC, `sub`, `user_id`, `session_id`, `iss`, `aud`, `iat`, `exp`, rol y permisos contra allowlists; falta probar que el dispatcher del Site elimine encabezados de identidad falsificados por navegador. |
| Authenticated E2E | PENDIENTE | Usuario real en Site publicado → puente server-side → FastAPI de staging → datos normalizados/SQLite. |
| Deployment Restart | PENDIENTE EN HOSTING | El test de recreación local y el smoke de volumen en CI no sustituyen la prueba del proveedor. |
| Authorization | PASS LOCAL / PENDIENTE WEB | Tests 401/403 para anónimo, lector que pide ADMIN, role/user spoof, firma alterada, audiencia, issuer y expiración. |
| Rollback | PENDIENTE EN HOSTING | Volver a imagen previa manteniendo el mismo volumen y comprobar `/api/ready`, WAPE y registro técnico. |
| GitHub Push + CI | Verificar en cada commit | `main`, repositorio `Towell_Forecast`, escáner, tests, cobertura, frontend y Docker. |

## Hallazgos de infraestructura

- El Site público existente aloja la interfaz, no el contenedor FastAPI. Su configuración de producción no tiene actualmente `PYTHON_ASSISTANT_URL` ni el secreto servidor-servidor configurados. No se debe activar el Assistant allí todavía.
- La imagen Docker guarda el estado en `STATE_DIR=/var/lib/forecast-towell`. Staging debe establecer `APP_ENV=staging`, `PERSISTENCE_MODE=hosted-volume`, `STATE_DIR` y `SQLITE_PATH` dentro del mismo volumen persistente. El guard de arranque comprueba la configuración de rutas, pero **no puede demostrar por sí solo que el proveedor montó un volumen durable**.
- Los archivos históricos E3 no están en Git ni en la imagen por privacidad. Antes del E2E de staging se deben transferir de forma privada los artefactos autorizados y verificar el hash del vintage `V-RUN-FENDI-2026-08-003`. Nunca copiar XLSX ni SQLite a un repositorio público.
- El Site publicado y `main` de GitHub tienen historias de despliegue distintas. La publicación web y el contenedor de staging deben provenir del mismo commit aprobado o quedar identificados por sus SHA en el manifiesto.

## Secuencia de validación hospedada

1. Elegir un hosting Docker con volumen persistente, HTTPS, gestor de secretos y control de restart/redeploy/rollback. Desplegar la imagen del commit de `main` con CI verde, un solo worker, `APP_ENV=staging` y el origen exacto del Site en `FRONTEND_URL`.
2. Configurar un secreto HMAC compartido **sólo en los servidores** y el ID gerencial autorizado. El frontend debe emitir tokens de 60 segundos para `aud=forecast-towell-fastapi` e `iss=forecast-towell-frontend:staging`; FastAPI resuelve el rol desde su allowlist. No transmitir token en query string ni registrarlo.
3. Verificar `/api/live`, `/api/health`, `/api/ready` y `/api/version` sobre HTTPS; documentar URL, commit, imagen, build time y latencias.
4. Hacer backup validado. Crear `TEST-PERSISTENCE-001` y una acción gerencial controlada con idempotency key; guardar IDs y hashes. Comprobar job, monthly run, research snapshot, vintage, bandas, métricas, evidencia y `run_logs` antes y después de restart, redeploy y crash seguro. Probar restore sobre una copia aislada, no sobre el estado productivo.
5. Desde navegador con sesión real, consultar Champion, WAPE, Forecast 12M y Assistant con contexto FENDI BD. Verificar 12 horizontes y origen de datos normalizados. Repetir con lector para lectura permitida y comando rechazado 403. Un anónimo debe obtener 401; alterar headers, rol, ID y token nunca debe elevar permisos.
6. Probar idempotencia y auditoría: la repetición devuelve el mismo job y persiste `duplicate_prevented` con actor, sesión y request ID. Confirmar que no se escribe el token completo en logs.
7. Simular Assistant degradado y DataProvider no disponible en staging controlado; comprobar forecast operativo en el primer caso y `/ready=503` en el segundo. Medir latencias hosteadas.
8. Revertir a la imagen previa sin borrar volumen; verificar health, ready, WAPE, vintage E3 y registro técnico. Registrar el resultado del rollback.

Ningún elemento pendiente se convierte en PASS por existir este runbook. OpenAI, Supabase, Deep Research real y voz permanecen OFF.
