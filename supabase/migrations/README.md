# Baseline pendiente de aprobación remota — PRD 09.2B

Los cuatro archivos SQL de este directorio son el baseline nuevo del contrato 09.2A. Las seis migraciones **legacy** se preservan sin cambios en `../legacy_migrations/`; no se aplicaron al proyecto nuevo. El historial remoto fue verificado vacío y `public` no contiene tablas de aplicación.

**No ejecutar `supabase db push` real hasta que el usuario apruebe expresamente la aplicación.** El `--dry-run` es sólo un gate de inspección. Véase [la matriz de reconciliación](../../docs/PRD09_2B_RECONCILIATION.md).
