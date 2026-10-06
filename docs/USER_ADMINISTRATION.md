# Administración de usuarios del portal

El módulo **Usuarios** está disponible sólo para perfiles `ADMIN` activos. La identidad permanece en Supabase Auth; `profiles` determina el rol global y `user_chain_access` los permisos por cadena. No se habilita registro público.

## Alta e invitación

1. El administrador captura nombre, correo, rol y, para `VIEWER` o `EDITOR`, al menos una cadena.
2. El navegador llama a la Edge Function `user-admin` con su sesión normal. La función verifica el JWT con Supabase Auth y vuelve a comprobar en base de datos que el actor sea `ADMIN` activo. No acepta roles de headers ni usa claves administrativas en Netlify/browser.
3. Para un correo nuevo, Supabase Auth envía la invitación con `redirectTo=https://towell-forecastia.netlify.app/update-password`. Una RPC exclusiva de `service_role` crea perfil y permisos en una transacción. Si esa transacción falla, se elimina únicamente la identidad Auth creada en esa solicitud.
4. Si Auth ya contiene el correo pero falta `profiles`, la misma acción completa el perfil y envía un enlace de recuperación para establecer contraseña. Esto cubre cuentas creadas previamente en el dashboard, como la de `jmrodriguez`.
5. El enlace de invitación o recuperación abre `/update-password`; el SDK verifica la sesión y `updateUser({ password })` establece la contraseña. Después se cierra la sesión del enlace y se vuelve a `/login`.

`VIEWER` recibe sólo lectura en las cadenas seleccionadas. `EDITOR` puede recibir, por cadena, edición, importación, ejecución de forecast y aprobación. `ADMIN` es global y no usa filas de `user_chain_access`. El servidor y la RPC validan rol, cadenas activas, duplicados y permisos incompatibles.

## Edición y baja

Un `ADMIN` puede modificar nombre, rol, estado y permisos de otros usuarios. No puede modificarse a sí mismo desde este módulo. La RPC reemplaza los permisos de cadena atómicamente y registra el cambio en `audit_log`.

**Eliminar acceso** significa baja segura y reversible: `profiles.status='INACTIVE'`, permisos de cadena eliminados y Auth conservado para trazabilidad. RLS deniega los datos operativos inmediatamente, incluso si un JWT anterior sigue vigente. Para reactivar, usar **Editar**, seleccionar `ACTIVE` y reasignar las cadenas. No se hace borrado físico de usuarios con referencias históricas.

## Despliegue y verificaciones

1. Aplicar `202610060001_portal_user_administration.sql` tras revisar el dry-run; no editar migraciones previas.
2. Desplegar sólo `user-admin` con verificación JWT habilitada (`npx supabase functions deploy user-admin --project-ref bskoyqhbgrycpwhydnnr`). No utilizar `--no-verify-jwt` ni `--prune`.
3. Confirmar que `https://towell-forecastia.netlify.app/update-password` figura en Redirect URLs y que el Site URL sigue siendo Netlify. Configurar SMTP propio si se supera el límite del remitente predeterminado de Supabase.
4. Verificar: usuario anónimo/VIEWER/EDITOR → 401/403; ADMIN → lista/invita/edita/baja; invitado → correo → contraseña → login; usuario inactivo → sin datos; historial y flags E3 sin cambios.

La Edge Function usa sólo los secretos que Supabase proporciona en su entorno alojado. No configurar `SUPABASE_SERVICE_ROLE_KEY` en Netlify, en `NEXT_PUBLIC_*` ni en archivos versionados. Railway y los flags de forecast permanecen sin cambios.
