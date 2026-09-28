# Migraciones Supabase — 09.2B y 09.2C

Las migraciones `001`–`004` son el baseline aprobado y aplicado de 09.2B (28 tablas y una vista). Permanecen inmutables. Las migraciones `005`–`006` implementan Auth/RLS y Storage privado de 09.2C; `007` refuerza el aislamiento de la función de aprobación sin editar la migración aplicada `005`. Las seis migraciones **legacy** permanecen archivadas y sin aplicar en `../legacy_migrations/`.

Antes de cada push se debe confirmar proyecto vinculado, historial remoto, inventario de objetos, pruebas locales y `db push --dry-run`. Véanse [la reconciliación de 09.2B](../../docs/PRD09_2B_RECONCILIATION.md) y [la política de seguridad de 09.2C](../../docs/PRD09_2C_AUTH_RLS_STORAGE.md).

La migration `008` (`202609280001_portal_published_history.sql`) añade únicamente
la vista de consulta histórica publicada. No cambia la vista temporal ni facts,
lotes, políticas o evidencia. Véase [el contrato de lectura del portal](../../docs/PRD09_2E11_PUBLISHED_HISTORICAL_PORTAL.md).

La migration `009` (`202609280002_portal_history_periods.sql`) añade sólo
`portal_history_periods`: pares distintos `chain_id, period`, derivados de 008,
con `security_invoker=true`, SELECT para authenticated y sin acceso public/anon.
No cambia 008, datos, políticas ni elegibilidad temporal. Véase
[la corrección del selector de periodos](../../docs/PRD09_2E13_PERIOD_FILTER_READ_FIX.md).
