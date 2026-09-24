# Railway staging/bootstrap — FastAPI

Este procedimiento prepara el **primer despliegue del backend**, no habilita el asistente público ni cierra el E2E del PRD 09.1. La fuente única es `towell2026ia/Towell_Forecast`, rama `main`, imagen `Dockerfile.api`. No crear otro backend ni subir Excel, SQLite, `.env` o credenciales. Seleccionar un commit con CI verde antes de desplegarlo; la conexión GitHub→Railway por sí sola no impone ese gate.

## Configuración manual en Railway Hobby

1. Crear un proyecto/servicio desde el repositorio GitHub `towell2026ia/Towell_Forecast` y elegir la rama `main`. En **Variables** del servicio, poner exactamente `RAILWAY_DOCKERFILE_PATH=Dockerfile.api`, porque Railway sólo detecta automáticamente un archivo llamado `Dockerfile` en la raíz. Confirmar en el build log que se usa `Dockerfile.api`. [Dockerfiles de Railway](https://docs.railway.com/builds/dockerfiles).
2. Capturar estas variables **en el servicio Railway**, no en Git ni en el frontend:

   ```dotenv
   APP_ENV=development
   API_HOST=0.0.0.0
   ASSISTANT_PROVIDER=local
   DATA_PROVIDER=normalized
   PERSISTENCE_PROVIDER=sqlite
   RESEARCH_PROVIDER=local
   SUPABASE_ENABLED=false
   OPENAI_ENABLED=false
   VOICE_ENABLED=false
   DEEP_RESEARCH_ENABLED=false
   AI_ASSISTANT_API_ENABLED=false
   RAILWAY_DOCKERFILE_PATH=Dockerfile.api
   ```

   **No capturar `PORT` ni `API_PORT`** en este bootstrap. Railway inyecta `PORT`; FastAPI usa `API_PORT` si existe, en su ausencia `PORT` y finalmente `8000`. Si en el futuro se fija `API_PORT`, deberá coincidir con el puerto al que Railway enruta el servicio. `API_HOST=0.0.0.0` es necesario para aceptar tráfico del proxy. [Puerto y enlace de Railway](https://docs.railway.com/networking/troubleshooting/application-failed-to-respond).
3. En Settings → Healthcheck, usar la ruta `/api/live`. El healthcheck interno del Dockerfile también consulta `/api/live` en el puerto efectivo. `/api/live` no depende de OpenAI/Supabase. [Healthchecks de Railway](https://docs.railway.com/deployments/healthchecks).
4. En Networking, generar el dominio HTTPS de Railway. Guardar esa URL como la futura URL del backend, pero **no conectar aún el Site público** ni activar `AI_ASSISTANT_API_ENABLED`.
5. Para la validación de persistencia del PRD 09.1, agregar un volumen del servicio montado en `/var/lib/forecast-towell` **antes** de crear datos de estado. La imagen ya usa `STATE_DIR=/var/lib/forecast-towell` y SQLite queda bajo esa ruta por defecto. Sin volumen, los cambios del filesystem del contenedor son efímeros. Un volumen no reemplaza un backup independiente. [Volúmenes de Railway](https://docs.railway.com/volumes).

Este `APP_ENV=development` es sólo un bootstrap técnico. En Railway, el adaptador local deniega las rutas autenticadas incluso si se envía manualmente `x-actor-id`; únicamente se verifican endpoints técnicos anónimos. Antes de pasar a `APP_ENV=staging` se necesitan `FRONTEND_URL` HTTPS exacto, `PERSISTENCE_MODE=hosted-volume`, volumen validado, secreto HMAC sólo en servidores, allowlists y pruebas de suplantación web. `API_HOST` debe seguir siendo `0.0.0.0`. No utilizar `APP_ENV=production` todavía.

## Verificación tras el primer despliegue

Abrir sobre el dominio Railway generado:

```text
GET /api/live      → 200, status ok
GET /api/health    → 200, status ok
GET /api/ready     → 200 si datos, modelos y SQLite locales están disponibles
GET /api/version   → APP_VERSION y commit
```

Railway proporciona `RAILWAY_GIT_COMMIT_SHA` en despliegues desde GitHub; el backend lo usa para `/api/version` si `GIT_SHA` no está definido. `APP_VERSION` puede definirse manualmente para etiquetar una release; si no se define, se usa la versión del código. Comparar el SHA devuelto con el commit desplegado. [Variables de Railway](https://docs.railway.com/variables/reference). Si `/api/ready` falla, revisar logs y la causa de `not_ready`; un `/api/live` exitoso sólo confirma proceso vivo.

## No configurar todavía

- No cargar `OPENAI_API_KEY`, claves Supabase ni credenciales de voz/Deep Research.
- No habilitar `OPENAI_ENABLED`, `SUPABASE_ENABLED`, `VOICE_ENABLED` ni `DEEP_RESEARCH_ENABLED`.
- No habilitar `AI_ASSISTANT_API_ENABLED` ni conectar el frontend publicado al dominio Railway durante bootstrap.
- No copiar XLSX privados, SQLite local o el vintage E3 por GitHub o variables de entorno. Su transferencia, si se aprueba, debe hacerse por un canal privado y con verificación de hashes.

## Restart, rollback y siguiente fase

Para una falla de proceso sin cambio de código, usar Restart y verificar los cuatro endpoints. Para volver a una versión anterior, en Deployments elegir la última release sana y usar Rollback; Railway restaura imagen y variables de esa release. Confirmar `/api/ready`, `/api/version` y que el volumen conserva su estado. Hacer backup antes de una prueba de restore o una migración. La disponibilidad del rollback depende de la retención de imágenes del plan. [Rollback de Railway](https://docs.railway.com/guides/roll-back-bad-deploy).

Después del bootstrap: validar reinicio/redeploy con volumen, identidad real del Site y E2E autenticado de staging según `docs/PRD09_1_DEPLOYMENT_VALIDATION.md`. Supabase vendrá **después**, con exportación, validación, importación y conciliación; no se conecta ni se migra durante este despliegue.
