# FORECAST Towell — PRD 02

> **ARCHIVO HISTÓRICO — NO EJECUTAR.** Este runbook describe el piloto anterior y sus pasos de puesta en operación ya no son válidos para el proyecto Supabase multichain. El SQL original se preserva en `supabase/legacy_migrations/` sólo como evidencia. El baseline vigente y su gate están en [PRD 09.2B](PRD09_2B_RECONCILIATION.md). No ejecutar ningún paso de migración de esta página.

## Estado de la entrega

La aplicación web, el esquema PostgreSQL para Supabase, las políticas de acceso,
el procedimiento de captura, el versionado, la auditoría, los eventos y el
importador idempotente están implementados. La publicación de datos oficiales
requiere un proyecto Supabase y sus dos variables de servidor.

## Puesta en operación histórica (no ejecutar)

1. Crear un proyecto Supabase para producción.
2. Históricamente se habría ejecutado `supabase/legacy_migrations/202609180001_prd02_base.sql`; **ahora está retirado y no debe aplicarse**.
3. Sustituir los correos `@towell.local` de los tres gerentes y vincular cada
   registro con su `auth.users.id`.
4. Configurar `SUPABASE_URL` y `SUPABASE_SERVICE_ROLE_KEY` únicamente como
   variables del servidor del sitio.
5. Ejecutar `node scripts/migrate-prd01-to-supabase.mjs`. El script consume
   `outputs/prd01_fendi_bd/data`; no lee los Excel originales.
6. Revisar `migration_issues`, resolver D01–D10 y aprobar el lote
   `PRD01-FENDI-BD-v1`.
7. Publicar únicamente los registros canónicos de FENDI BD y cambiar el lote a
   `disabled` para impedir una segunda importación ordinaria.

## Controles implementados

- Llaves idempotentes por `batch_key`, `file_hash` y `record_hash`.
- Las dos versiones de diciembre de 2024 se conservan como archivos fuente y
  registros distintos.
- ITEM y UPC se almacenan como texto.
- Las cantidades admiten cero, pero nunca se sustituyen faltantes por cero.
- Los editores solo pueden guardar en periodos `open` o `reopened`.
- Pedido, Venta, Entrega y Fcst Cliente generan versión, auditoría y evento.
- Entrega exige línea de pedido y no puede exceder el saldo pendiente.
- Las tablas de motores permanecen vacías para el PRD 03.
- El bucket `migration-evidence` es privado y de solo lectura para gerentes.

## Límites intencionales

No se implementan motores de pronóstico, WAPE, backtesting, percentiles,
investigación, agentes, voz ni alertas. La interfaz los omite para conservar la
frontera del PRD 02.
