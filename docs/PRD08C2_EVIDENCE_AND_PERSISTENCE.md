# PRD 08C.2: evidencia, vintages y persistencia local

## Resultado auditado (23 de septiembre de 2026)

- Fuente original: `Estadistica Todas las Cadenas Cierre de Julio 2026.xlsx`, 6,961,314 bytes, SHA-256 `fc11daae57969e293d305c5df673368463e3aea2b69e264c9832a17d07c001fb`.
- Manifiesto E3: `EM-daa1ced6748c148ddb6c`. Fecha local de creación: 2026-08-12 18:25:58 UTC; última escritura: 2026-08-12 18:26:01 UTC; 346 celdas contrastadas con el XLSX original. La fecha local no es prueba inmutable de recepción corporativa.
- Vintage auditado: `V-RUN-FENDI-2026-08-003`, corte 2026-08-12, hash `94656300368c85c67ad5dd8ec9b060685ddff58403e502b1f7f98a174bed8e81`, 12 horizontes, bandas P10/P50/P90/P95, fuga temporal 0. Registro: `FROZEN`, `E3`, `VALID_WITH_DOCUMENTED_LOCAL_EVIDENCE`.
- Existe también `V-RUN-FENDI-2026-08-002`, un rerun congelado del **mismo corte**. Son dos artefactos, no dos meses históricos distintos.
- Expansión 2023-01 a 2026-08: 0 nuevos cortes; 43 cortes sin evidencia temporal registrada y el corte de agosto ya registrado. Los 192 registros FENDI de 2024 continúan `availability_unknown`.
- Una segunda copia local del XLSX de julio de 2026 tiene el mismo SHA-256 pero fecha local posterior. Corrobora identidad de bytes, no constituye E2 ni adelanta disponibilidad.

La búsqueda local se limitó a Downloads y al proyecto/OneDrive. Los libros de cierre 2024 y febrero 2025 se recibieron localmente el 14 de septiembre de 2026; el campo `core.xml` de modificación anterior no acredita cuándo estuvieron disponibles para el forecast. No se consultaron correo, SharePoint ni repositorios corporativos porque no hay conexión autorizada en este entorno. Tampoco se afirma que no existan allí.

## Niveles y controles

`E1` es log corporativo inmutable; `E2`, recepción o repositorio externo verificable; `E3`, archivo original con hash, metadatos locales y revisión documentada; `E4`, regla operativa documentada y condicionada; `E5`, inferencia bloqueada; `E6`, desconocido bloqueado. `EvidenceRegistry` guarda cada manifiesto bajo ID derivado de su contenido. Una evidencia E1/E2 posterior se agrega por separado, tras verificación externa, y no modifica E3. El SHA-256 identifica bytes; por sí solo no autentica la fecha de recepción.

`VintageRegistry` usa transiciones unidireccionales `CANDIDATE → TEMPORALLY_VALID → VALIDATED → FROZEN`; un candidato puede pasar a `REJECTED`. Los artefactos congelados originales no se reescriben. La entrada de registro conserva corte, evidencia, snapshots, versión, Champion, versiones de modelos y horizontes.

## Persistencia y operación

`PersistenceProvider` es independiente de `DataProvider`. `LocalPersistenceProvider` usa SQLite en `services/assistant_api/state/historical/historical.sqlite3` (estado local ignorado por Git). Tiene entidades separadas para evidencia, reglas y auditorías de disponibilidad, snapshots, runs, versiones, Champion, vintages, horizontes, bandas, evaluaciones, métricas y logs. Los registros de evidencia y artefactos congelados son insert-only a nivel de aplicación; SQLite local no es almacenamiento a prueba de manipulación externa. `SupabasePersistenceProvider` es un límite de interfaz que falla explícitamente: no se conecta a Supabase ni usa credenciales.

Comandos desde la raíz del proyecto:

```powershell
python -m services.assistant_api evidence-harden --source-dir 'C:\Users\franh\Downloads'
python -m services.assistant_api vintage-registry
python -m services.assistant_api vintage-expand --source-dir 'C:\Users\franh\Downloads' --start 2023-01 --end 2026-08
```

El endurecimiento vuelve a verificar SHA-256 y celdas antes de registrar E3. La expansión sólo considera fechas con manifiesto y disponibilidad temporal; una ejecución completada sin evidencia asociable no se cuenta como vintage registrado. Para un despliegue futuro se debe aprovisionar el archivo fuente autorizado o importar manifests previamente verificados y migrar el proveedor de persistencia; el XLSX privado y la base SQLite local no se publican en Git.

PRD 08C se considera cerrado en su criterio técnico: runner operativo, gate auditado, al menos un corte defendible y trazabilidad/persistencia preparadas. No equivale a backtest oficial E1/E2 ni a 44 vintages históricos.
