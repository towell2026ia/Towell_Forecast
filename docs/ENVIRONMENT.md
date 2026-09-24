# Entornos y configuración del servicio Python

`Settings` en `services/assistant_api/settings.py` es la única lectura de variables de entorno operativas del backend. Rechaza valores inválidos al arrancar. El archivo `.env.example` es una plantilla sin credenciales; el proceso no lo carga automáticamente. Inyectar variables mediante el gestor del hosting o un archivo local ignorado por Git.

| Variable | Desarrollo | Staging / producción |
| --- | --- | --- |
| `APP_ENV` | `development` o `test` | `staging` o `production` |
| `API_HOST`, `API_PORT` | `127.0.0.1`, `8000` | interfaz/puerto del contenedor |
| `FRONTEND_URL` | `http://localhost:3000` | origen HTTPS exacto, sin `*` |
| `DATA_DIR` | `app/data` del repositorio | ruta absoluta con snapshots autorizados |
| `STATE_DIR`, `SQLITE_PATH` | estado local ignorado por Git | volumen persistente, ruta absoluta |
| `PERSISTENCE_MODE` | `local` | `hosted-volume`; SQLite debe estar dentro de `STATE_DIR` |
| `ASSISTANT_PROVIDER`, `DATA_PROVIDER`, `PERSISTENCE_PROVIDER`, `RESEARCH_PROVIDER` | `local`, `normalized`, `sqlite`, `local` | iguales durante PRD 09 |
| `ASSISTANT_API_TOKEN` | opcional sólo en loopback | secreto HMAC servidor-servidor de al menos 32 caracteres |
| `ASSISTANT_MANAGER_IDS` | `local-manager` implícito si no hay listas | lista de IDs verificados por el hosting, obligatoria |
| `ASSISTANT_EDITOR_IDS`, `ASSISTANT_READER_IDS` | opcionales | listas autorizadas explícitas |
| `AI_ASSISTANT_UI_ENABLED`, `AI_ASSISTANT_API_ENABLED` | `true` | habilitar sólo tras verificar identidad y backend |
| `HISTORICAL_RUNNER_ENABLED`, `MONTHLY_RUNNER_ENABLED`, `LOCAL_RESEARCH_ENABLED`, `LOCAL_INTENT_ROUTER_ENABLED` | `true` | por función, revisados en backend |
| `OPENAI_ENABLED`, `SUPABASE_ENABLED`, `VOICE_ENABLED`, `DEEP_RESEARCH_ENABLED` | `false` | `false`; el arranque rechaza `true` |
| `MAX_FORECAST_RUNS`, `MAX_HISTORICAL_RUNS` | `2`, `1` | límites por proceso, un worker para SQLite |
| `ASSISTANT_RATE_PER_MINUTE`, `FORECAST_RATE_PER_MINUTE`, `HISTORICAL_RATE_PER_MINUTE` | `60`, `10`, `4` | límites por usuario y ruta, por proceso |
| `EXTERNAL_TIMEOUT_SECONDS`, `RETRY_MAX_ATTEMPTS` | `10`, `3` | infraestructura futura; retries son opt-in |
| `APP_VERSION`, `GIT_SHA`, `BUILD_TIME` | etiqueta local | inyectar en cada release |

`test` usa los mismos proveedores locales, con rutas temporales inyectadas en pruebas. `staging` y `production` exigen origen HTTPS, secreto HMAC, allowlist gerencial y modo de volumen. Este último valida la ruta configurada, pero la durabilidad del montaje requiere la prueba real del proveedor. `production` deshabilita `/docs`, `/redoc` y `/openapi.json`. No existe conmutación silenciosa a OpenAI o Supabase.

El frontend necesita `PYTHON_ASSISTANT_URL`, `APP_ENV` igual al backend y el mismo `ASSISTANT_API_TOKEN` sólo en el servidor, nunca en variables `NEXT_PUBLIC_*`. En un hosting ChatGPT Site, el adaptador de identidad lee el ID de usuario autenticado del lado servidor y firma `sub`, `user_id`, `session_id`, `iss`, `aud`, `iat`, `exp`, rol y permisos. FastAPI compara rol y permisos con allowlists propias. Antes de habilitarlo públicamente hay que demostrar que el proxy del hosting elimina encabezados de identidad enviados por el navegador. Sin esa garantía, mantener `AI_ASSISTANT_API_ENABLED=false`.
