# Despliegue reproducible

## Instalación local

Requiere Python 3.12+, Node 22+, fuentes normalizadas autorizadas en `app/data` y permisos de escritura en `STATE_DIR`. No requiere VS Code, OneDrive, OpenAI ni Supabase.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r services\assistant_api\requirements.txt
./.venv/Scripts/python.exe -m services.assistant_api.serve
```

En otra terminal: `npm ci` y `npm run dev`. Verificar `GET http://127.0.0.1:8000/api/live`, `/api/health`, `/api/ready`, `/api/version` y una consulta WAPE autenticada. En Linux/macOS usar `.venv/bin/python`.

## Contenedor FastAPI

```sh
docker build -f Dockerfile.api -t forecast-towell-api:<commit> .
docker run --rm -p 8000:8000 -v forecast-state:/var/lib/forecast-towell \
  -e APP_ENV=development -e API_HOST=0.0.0.0 forecast-towell-api:<commit>
```

El contenedor ejecuta un usuario sin privilegios y no contiene XLSX privados, `.env` ni SQLite local. Staging/production requieren variables del gestor de secretos, `FRONTEND_URL` HTTPS, allowlist gerencial y volumen persistente. Limitar a **un proceso/worker** mientras jobs, rate limits e idempotencia dependan de coordinación local. La imagen es preparación de hosting: no se desplegó ni se conectó OpenAI/Supabase durante PRD 09.

## Verificación previa

```sh
python scripts/check-public-repo.py
python -m unittest discover -s services/assistant_api -p 'test_*.py' -q
npm run lint
npx tsc --noEmit
npm run build
```

CI ejecuta lo anterior, tests de motores, auditorías de dependencias y `docker build` en cada push/PR. Sólo desplegar un commit cuya CI completa esté verde. `GET /api/live` confirma proceso; `GET /api/ready` confirma datos, modelos, autenticación y SQLite. Un assistant degradado no bloquea forecast; datos o SQLite no disponibles devuelven `not_ready`/503.

## Release y rollback

Convención: `vMAJOR.MINOR.PATCH` para releases aprobados; `APP_VERSION`, `GIT_SHA` y `BUILD_TIME` identifican cada imagen. No sobrescribir tags. Hacer backup validado de SQLite antes del cambio; guardar también evidencia, manifests y snapshots. Conservar al menos la imagen anterior. Para rollback, enrutar tráfico a la imagen anterior, con la misma base compatible y verificar `/api/ready` y WAPE. No ejecutar migraciones destructivas automáticas. Si cambió el esquema, usar backup/restore aprobado y un ensayo de restauración previo. No usar `git reset --hard` como procedimiento de rollback.

La conexión de Supabase requerirá exportar, validar, importar con confirmación y reconciliar checksums antes de redirigir tráfico. El adaptador actual permanece deshabilitado.
