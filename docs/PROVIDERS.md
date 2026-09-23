# Contratos de proveedores

`DataProvider` lee snapshots operativos y normalizados. `PersistenceProvider` guarda artefactos, auditoría, jobs y vintages. `ResearchProvider` resuelve evidencia por cutoff. `AssistantProvider` interpreta respuestas. `AuthProvider` autentica; `authorize()` comprueba permisos. `TelemetryProvider` registra eventos y métricas. No se mezclan lectura operativa, escritura derivada e identidad.

| Contrato | PRD 09 | Futuro, apagado |
| --- | --- | --- |
| Assistant | `LocalAssistantProvider` | `OpenAIAssistantProvider` (lanza `provider_disabled`) |
| Data | `NormalizedDataProvider` | `SupabaseDataProvider` (lanza `supabase_disabled`) |
| Persistence | `LocalPersistenceProvider` (SQLite) | `SupabasePersistenceProvider` (lanza `supabase_persistence_not_connected`) |
| Research | `LocalResearchProvider` | `OpenAIDeepResearchProvider` (lanza `provider_disabled`) |
| Auth | `LocalAuthProvider` o `SignedAuthProvider` | `SupabaseAuthProvider` sin conexión |
| Voice | `DisabledVoiceProvider` | implementación no contratada |
| Telemetry | `LocalTelemetryProvider` | APM/hosting no conectado |

El esquema de persistencia mantiene entidades `source_evidence`, `availability_rules`, `availability_audits`, `research_snapshots`, `data_snapshots`, `historical_runs`, `monthly_runs`, `model_versions`, `champion_history`, `forecast_vintages`, `forecast_horizons`, `forecast_bands`, `actual_evaluations`, `performance_metrics`, `run_logs`, además de `vintage_registry` y `forecast_jobs`. `persistence_migration.py` exporta entradas con keys, valida checksum, ofrece importación explícita y reconciliación. No se ejecuta migración a Supabase en PRD 09.

Para cambiar proveedor en el futuro: implementar el mismo contrato y `health()`, probar paridad de datos, exportar/validar/importar/reconciliar en staging, activar sólo después de revisión de seguridad y rollback. En esta versión `Settings` rechaza la selección de proveedores remotos antes de servir tráfico.
