# Baseline local de desempeño (23-sep-2026)

Mediciones exploratorias en la máquina de desarrollo, una sola ejecución por consulta HTTP; no son SLO ni objetivos de producción. Servidor FastAPI standalone en `127.0.0.1:8765`, `APP_ENV=test`, proveedor normalizado/SQLite local. El primer `/api/live` incluye arranque/conexión del cliente PowerShell. Las mediciones siguientes fueron `Invoke-WebRequest` consecutivos:

| Operación | Estado | Latencia cliente |
| --- | --- | ---: |
| `/api/live` primera consulta | 200 | 193.6 ms |
| `/api/health` | 200 | 4.6 ms |
| `/api/ready` | 200 | 13.8 ms |
| `/api/version` | 200 | 4.3 ms |
| `/api/performance/wape` | 200 | 9.9 ms |
| `/api/models/champion` | 200 | 8.0 ms |
| `/api/forecast/12m` | 200 | 6.2 ms |

Corrida real del motor mensual de julio 2026 en un directorio temporal (sin tocar Champion publicado): 26.326 s, `Completed`, 12 horizontes. Revalidación cacheada del vintage histórico E3 de agosto 2026: 1.222 s, `VALID_WITH_DOCUMENTED_LOCAL_EVIDENCE`; esto **no** mide un entrenamiento histórico nuevo. El artefacto histórico original registra 22.9973 s para su ejecución anterior (PRD 08C.1), pero hardware/carga podrían diferir.

Dashboard: servidor frontend local arrancó en `http://localhost:5173` y el build completó. La carga autenticada del Dashboard no se cronometró de forma comparable porque requiere sesión de hosting; queda como baseline pendiente antes de despliegue. No establecer umbrales a partir de estas muestras. Repetir con varias iteraciones, mismo hardware, estado frío/caliente identificado y usuarios de staging antes de comparar releases.
