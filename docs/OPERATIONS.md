# Operación y recuperación

Los logs JSON incluyen `timestamp`, `level`, `environment`, `request_id`, `user_id`, `component`, `event`, `duration`, `status` y `error_code`. Las corridas usan `run_id`. No registrar mensaje completo del assistant, tokens ni contenido de archivos. `X-Request-ID` permite correlacionar una respuesta con el log. Métricas locales en memoria: solicitudes, latencias, errores, llamadas del assistant y runs; un reinicio las reinicia.

## Respaldo y restauración SQLite

Detener admisión de jobs y tomar un backup en un destino nuevo fuera del repositorio:

```sh
python -m services.assistant_api.persistence_admin backup /secure/backups/forecast-YYYYMMDD.sqlite3
python -m services.assistant_api.persistence_admin export /secure/backups/forecast-YYYYMMDD.json
python -m services.assistant_api.persistence_admin validate /secure/backups/forecast-YYYYMMDD.json
```

`backup()` usa SQLite Backup API y `PRAGMA integrity_check`. La restauración exige confirmación explícita y crea `historical.sqlite3.pre-restore.bak` como copia de seguridad previa; aborta si ya existe para no sobrescribirla. Probarla primero en un entorno temporal:

```sh
python -m services.assistant_api.persistence_admin --database /tmp/restore-test.sqlite3 restore /secure/backups/forecast-YYYYMMDD.sqlite3 --confirm
```

Verificar integridad, recuentos, manifiesto y `/api/ready` antes de restaurar producción. Los JSON exportados contienen datos operativos privados: guardarlos fuera de Git con controles equivalentes a SQLite. Conservar aparte el XLSX fuente autorizado o la ruta lógica y SHA-256 para volver a verificar E3.

## Runbook

| Síntoma | Diagnóstico y acción segura |
| --- | --- |
| FastAPI no responde | Revisar proceso/contenedor, puerto y `/api/live`; leer error de configuración de arranque sin imprimir secretos. |
| `/api/ready` 503 | Revisar campo `providers`: datos/modelos, SQLite, permisos de volumen y configuración. No enrutar tráfico. |
| SQLite bloqueada | Comprobar jobs activos y procesos duplicados; mantener un worker. No borrar WAL/SHM ni forzar cierre durante una transacción. Hacer backup cuando se libere. |
| Runner falló | Consultar job y `run_id`, `error_code` y logs. No forzar rerun sin permiso ADMIN ni alterar vintage congelado. |
| Forecast falla | Confirmar fuentes normalizadas, cutoff y modelos. Un fallo del assistant no justifica cambiar Champion. |
| Assistant falla | La API responde `ASSISTANT_001`/503; Forecast puede seguir UP. Revisar proveedor local y datos, sin devolver respuestas simuladas. |
| Fuente no encontrada | Mantener corte bloqueado. Solicitar archivo autorizado y verificar hash/fecha; no inferir `available_at`. |

Los endpoints de comandos devuelven 202 y un job; se consulta su estado por `GET /api/forecast/jobs/{id}` o `GET /api/historical/jobs/{id}`. Clave `Idempotency-Key` obligatoria. Estados: `QUEUED`, `RUNNING`, `COMPLETED`, `FAILED`; la cancelación aún no está expuesta como comando HTTP. Límite concurrente local: `MAX_FORECAST_RUNS` y `MAX_HISTORICAL_RUNS`.
