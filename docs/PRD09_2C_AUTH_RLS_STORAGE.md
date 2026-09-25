# PRD 09.2C — Supabase Auth, RLS y Storage

Proyecto vinculado: `bskoyqhbgrycpwhydnnr`. Esta fase añade `202609250005_auth_rls.sql`, `202609250006_private_storage.sql` y la migración correctiva `202609250007_approval_probe_guard.sql` después de las cuatro migraciones inmutables de 09.2B. La 007 evita que una llamada directa a la función de aprobación revele propuestas de otra cadena. No conecta Railway ni carga datos reales.

## Identidad y administración

Supabase Auth (`auth.users`) es la única fuente de identidad. `public.profiles.id` referencia `auth.users.id`; `public.user_chain_access` concede permisos por cadena. No existe `public.users` ni se almacena ninguna contraseña fuera de Auth. Las políticas emplean `auth.uid()` del JWT verificado por Supabase; no confían en email, rol o cadena declarados en headers del navegador. Las funciones auxiliares usan `SECURITY DEFINER`, `search_path=''`, referencias calificadas y viven en el esquema no expuesto `private`.

Un `ADMIN` activo puede administrar cadenas, perfiles y accesos de otros usuarios. No puede cambiar sus propios campos de rol/permisos mediante la API de usuario. `EDITOR` y `VIEWER` necesitan un registro explícito en `user_chain_access`; `VIEWER` nunca escribe aunque un flag de escritura esté activado por error. `INACTIVE` no obtiene filas operativas.

Alta inicial: un operador con acceso confiable al SQL Editor crea primero el usuario en Supabase Auth y, en una transacción, inserta su UUID en `public.profiles` con `global_role='ADMIN'`. No se permite el bootstrap de ADMIN desde una solicitud anónima. Después, el ADMIN asigna perfiles y permisos de otras personas; para EDITOR/VIEWER debe crear `user_chain_access` por cada cadena. No publicar claves de servicio en navegador, repositorio o logs.

Baja: cambiar `profiles.status` a `INACTIVE` desde una sesión ADMIN distinta o un operador confiable, revocar sesiones desde Supabase Auth y revisar accesos. La RLS bloquea inmediatamente el acceso a filas incluso si un JWT previo aún no expiró. Para recuperación de un único ADMIN perdido, usar un operador de base de datos confiable; nunca abrir una política de autoasignación.

## Matriz de permisos

| Operación | VIEWER | EDITOR | ADMIN | Servicio |
| --- | --- | --- | --- | --- |
| Leer cadena y resultados | `can_view` | `can_view` | Global | Sí |
| Editar categorías/productos | No | `can_edit` | Global | Sí |
| Crear perfiles y versiones de importación; solicitar batch | No | `can_import` | Global | Sí |
| Subir archivos fuente/evidencia de investigación | No | `can_import` | Global | Sí |
| Solicitar job `FORECAST_RUN` en estado `QUEUED` | No | `can_run_forecast` | Global | Sí |
| Proponer decisión versionada | No | `can_edit` | Global | Sí |
| Aprobar propuesta mediante nueva versión | No | `can_approve` | Global | Sí |
| Crear resultados del motor, observaciones, evidencias temporales o logs | No | No | No por API de usuario | Sí |
| Gestionar profiles/user_chain_access de terceros | No | No | Sí | Sí |

Un usuario con escritura también necesita `can_view` para observar o actualizar una fila preexistente. Ningún usuario obtiene `DELETE` en tablas operativas, salvo ADMIN para revocar `user_chain_access` ajeno. Los grants de columnas impiden cambiar por UPDATE la identidad de producto, cadena o lineage. `forecast_decisions` permanece append-only por 09.2B: aprobación significa INSERT de la versión siguiente sobre una propuesta abierta, no UPDATE.

Las 28 tablas conservan RLS habilitada. `anon` no recibe grants operativos; `authenticated` recibe sólo los grants explícitos de 09.2C. La vista `monthly_observations_current` sigue `security_invoker=true` y hereda RLS de observaciones. `run_logs` se autoriza mediante los padres `jobs`/`forecast_runs`; `audit_log` y `legacy_identity_map` sólo son visibles para ADMIN activo.

## Storage privado

Los buckets `source-files`, `research-evidence`, `model-artifacts` y `exports` se crean con `public=false`. La ruta obligatoria para objetos nuevos es `chain_uuid/YYYY-MM/batch_uuid/safe_filename`. El parser rechaza rutas mal formadas antes de convertir el UUID. Las lecturas requieren `can_view` de la cadena del path. Las inserciones de usuarios sólo están disponibles en `source-files` y `research-evidence` con `can_import`; `model-artifacts` y `exports` son escrituras exclusivas del servicio. No hay políticas de usuario para UPDATE/DELETE, por lo que no se puede sobrescribir ni borrar un objeto desde un token de usuario. Los enlaces firmados deben emitirse posteriormente sólo después de la misma autorización de cadena; esta fase no implementa ese endpoint.

## UserScopedClient y ServiceClient

El futuro `UserScopedClient` debe reenviar el JWT verificado del usuario y dejar que RLS filtre filas. El `ServiceClient` usa credencial de servicio sólo dentro de un proceso backend confiable para trabajos, cálculos y persistencia; nunca debe convertir una petición ordinaria en una consulta ilimitada. Antes de ejecutar un trabajo solicitado por usuario, el backend debe comprobar de nuevo permiso, cadena y estado activo. 09.2C no introduce ningún cliente de runtime ni activa `SUPABASE_ENABLED`.

## Validación y riesgos

`npm run test:supabase-baseline` verifica el contrato congelado 09.2B. `npm run test:supabase-security` aplica las siete migraciones a PostgreSQL aislado (PGlite), crea identidades sintéticas y ejecuta CP01–CP21 como roles `anon`, `authenticated` y `service_role`: grants, RLS, aislamiento entre cadenas, permisos de importación/ejecución/aprobación, Storage, autoescalada y prueba negativa del helper de aprobación. No carga datos reales y no sustituye las pruebas de un navegador con sesión firmada, que corresponden al cutover posterior.

Riesgos residuales: `service_role` omite RLS y nunca debe salir del backend; la integridad entre un objeto Storage y su registro de importación deberá validarse en el futuro runtime; un administrador de base de datos puede cambiar políticas o perfiles y requiere controles operativos externos. Supabase Auth verifica la firma/expiración del JWT, mientras estas pruebas SQL comprueban las decisiones de autorización posteriores.

Recuperación: si una política nueva causa bloqueo, usar SQL Editor como operador de confianza para desactivar temporalmente el perfil afectado o aplicar una migración compensatoria revisada. No borrar ni editar las migraciones ya aplicadas. Para revertir un despliegue completo, restaurar un respaldo verificado y repetir la auditoría de migraciones, tablas, RLS y buckets privados antes de reabrir acceso. No hacer `db reset` en el proyecto remoto.

Estado de runtime durante esta fase: `SUPABASE_ENABLED=false`, `PERSISTENCE_PROVIDER=sqlite`, `DATA_PROVIDER=normalized`; sin OpenAI, Deep Research, voz, Netlify ni cutover.
