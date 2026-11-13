# Consulta de la fuente oficial en desarrollo local

La adaptación parte de `7594a58fefdf7fcbf195c0739d615f8825ff868f`. Conserva los componentes del portal, los RPC revisados, sus políticas RLS y los motores existentes. No incorpora un motor ni una aplicación alternativa.

`LOCAL_POSTGRES_CONFIG` habilita el adaptador únicamente con `APP_ENV=development` o `test`, `API_HOST=127.0.0.1`, ausencia de identificadores Railway y `SUPABASE_URL=http://127.0.0.1:<API_PORT>`. Producción y staging rechazan esta configuración antes de leer credenciales. La dependencia PostgreSQL es opcional: `services/assistant_api/requirements-local.txt`; el Dockerfile de Railway no la instala.

La configuración privada identifica host, puerto, base `gateb_official_*`, directorio, marcador de laboratorio e identificador de clúster. Cada conexión comprueba esos valores. La contraseña local se mantiene en un archivo binario Windows DPAPI del usuario, fuera de Git. Las consultas comerciales nunca se enrutan a una base productiva; el modo híbrido descrito abajo utiliza exclusivamente el servicio Auth hospedado para verificar identidad.

El gateway expone el subconjunto REST utilizado por el portal y las comprobaciones locales de usuario/logout. La autenticación híbrida sustituye los tokens fixture anteriores: valida identidad con Auth hospedado y conserva la autorización en PostgreSQL local. Las consultas comerciales se ejecutan en una transacción de sólo lectura, con `authenticated`, `auth.uid()` y las políticas RLS vigentes. Los filtros se traducen a identificadores validados y parámetros SQL. No existe un endpoint SQL general. Las escrituras REST, las funciones de publicación y las tablas históricas crudas quedan cerradas.

El backend usa ese mismo gateway en su transporte de lectura. El navegador consulta las mismas rutas mediante HTTP loopback. La excepción del frontend exige simultáneamente `NODE_ENV=development`, `NEXT_PUBLIC_LOCAL_POSTGRES_ENABLED=true` y el origen canónico `http://127.0.0.1:8000`. Ningún flag habilita HTTP remoto ni modifica los controles productivos.

## Arranque

Utilizar la instancia PostgreSQL y la configuración privadas preparadas por el operador. No ejecutar el inicializador sintético de Gate B sobre datos comerciales. La instalación y carga de ensayo reutilizan `AuthorizedOfficialConnection(local_rehearsal=True)` y los dos SQL de `lab/official_source/cutover`, en una sola transacción exterior con `autocommit=True`.

Backend, después de configurar las rutas privadas, credencial y flags de lectura:

```powershell
python -B -m uvicorn services.assistant_api.api:app --host 127.0.0.1 --port 8000
```

Frontend con el mismo Next.js usado por el despliegue Netlify, sin build remoto:

```powershell
$env:NEXT_PUBLIC_SUPABASE_URL='https://bskoyqhbgrycpwhydnnr.supabase.co'
# NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY procede de la configuración privada existente.
$env:NEXT_PUBLIC_LOCAL_DATA_URL='http://127.0.0.1:8000'
$env:NEXT_PUBLIC_LOCAL_DATA_PUBLISHABLE_KEY='sb_publishable_local_development_only'
$env:NEXT_PUBLIC_LOCAL_POSTGRES_ENABLED='true'
$env:NEXT_PUBLIC_FORECAST_API_URL='http://127.0.0.1:8000'
$env:NEXT_PUBLIC_SITE_URL='http://localhost:5173'
$env:NEXT_TELEMETRY_DISABLED='1'
& ./node_modules/.bin/next.cmd dev --hostname 127.0.0.1 --port 5173
```

El marcador de datos `sb_publishable_local_development_only` no es una API Key de un proveedor ni acredita identidad. Auth requiere su clave publicable real existente, sin crear otra. Contraseñas y sesiones nunca se incluyen en estas instrucciones. No configurar `LOCAL_POSTGRES_CONFIG` ni los flags del navegador en Netlify o Railway.

## Identidad hospedada con datos locales

El modo híbrido requiere configuración explícita completa; una configuración incompleta se rechaza, sin volver a tokens fixture ni consultar datos productivos.

| Configuración | Destino |
|---|---|
| Backend `SUPABASE_URL` y `LOCAL_POSTGRES_CONFIG` | Gateway y PostgreSQL locales ya preparados |
| Backend `LOCAL_SUPABASE_AUTH_URL` | Exclusivamente `https://bskoyqhbgrycpwhydnnr.supabase.co` |
| Backend `LOCAL_SUPABASE_AUTH_PUBLISHABLE_KEY` | Clave publicable existente de ese proyecto, nunca service role |
| Frontend `NEXT_PUBLIC_SUPABASE_URL` y `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | Auth del proyecto hospedado |
| Frontend `NEXT_PUBLIC_LOCAL_DATA_URL` y `NEXT_PUBLIC_LOCAL_DATA_PUBLISHABLE_KEY` | REST del gateway `http://127.0.0.1:8000` |
| Frontend `NODE_ENV=development` y `NEXT_PUBLIC_LOCAL_POSTGRES_ENABLED=true` | Guardas obligatorias del modo local |

No colocar contraseñas de usuarios en archivos de configuración, comandos, pruebas, Git ni reportes. El usuario inicia sesión mediante el formulario existente contra Auth. El SDK de datos recibe el access token actualizado de Auth; no inicia otra sesión ni utiliza la clave publicable como identidad. Las consultas de catálogo, observaciones, inventario, cálculos y permisos se mantienen en localhost. Tampoco se habilitan rutas operativas hospedadas, comprobaciones de base remota ni administración de usuarios productivos.

`HostedAuthVerifier` sólo permite `GET /auth/v1/user` del proyecto exacto: HTTPS con certificados verificados, sin redirecciones ni proxy tomado del entorno. Cada aceptación requiere una nueva respuesta de Auth; no existe aceptación offline ni caché de identidad o permisos. Antes de la petición se validan issuer, audiencia y rol `authenticated`, UUID de usuario y sesión, caducidad, emisión y `nbf` cuando exista. Se rechazan identidades anónimas y `alg=none`. La respuesta debe corresponder al mismo UUID del token. Un rechazo de Auth falla con 401; su indisponibilidad falla con 503. No se registran tokens, contraseñas ni claves.

Autenticar un UUID hospedado no le otorga permisos locales. Se requiere su correspondencia exacta con un perfil local `ACTIVE` y las concesiones locales existentes de `user_chain_access`. No asociar por email a un administrador fixture, no copiar roles del navegador o de `user_metadata`, no crear cuentas ni conceder ADMIN automáticamente. Cada consulta conserva el rol SQL `authenticated`, `auth.uid()` del usuario verificado, las políticas RLS y la transacción de sólo lectura. Un UUID sin perfil acreditado debe permanecer bloqueado hasta una decisión expresa sobre su correspondencia y permisos.

Al cerrar sesión se notifica al endpoint local con el token vigente, se bloquea su `session_id` en memoria hasta la caducidad presentada y se ejecuta `signOut({scope: "local"})` en Auth. El frontend elimina su sesión y descarta repositorios y resultados anteriores. El bloqueo local no persiste reinicios y no acredita revocación global ni cierre de otros dispositivos. Supabase documenta que los access tokens pueden seguir vigentes hasta `exp`; comprobar la sesión hospedada para garantizar revocación inmediata externa requeriría un alcance adicional, no simulado con fixtures. Véanse [sesiones](https://supabase.com/docs/guides/auth/sessions) y [sign out](https://supabase.com/docs/guides/auth/signout).

Las pruebas automatizadas usan respuestas Auth y JWT sintéticos y no certifican un inicio de sesión hospedado real. Deben cubrir JWT inválido o caducado, issuer/audiencia/UUID erróneos, caída o redirección de Auth, rechazo de service role, logout local, refresh, perfiles inactivos o ausentes, manipulación del rol, aislamiento por cadena y ausencia de consultas REST productivas. La validación real de login, refresh y logout requiere una sesión iniciada por el usuario y un perfil local expresamente acreditado; hasta entonces se reporta **NO ENSAYADA**, aunque pasen los mocks y las pruebas RLS locales.

## Límites de evidencia

- Una restauración del ensayo local acredita esa fuente y el alcance del respaldo ensayado. No acredita una recuperación integral del Supabase productivo, Storage ni Auth gestionado.
- Importar resultados previamente calculados conserva su emisión original por scope como procedencia. La fecha de importación SQL es una fecha diferente. No se certifica WAPE LIVE ni se publica un Champion.
- La investigación real mantiene la clave existente en Railway. No se utiliza una clave alternativa local. El ensayo sin acceso a esa clave debe informar OpenAI/Deep Research deshabilitados, sin sustituirlos por respuestas ficticias.
- Los comandos productivos siguen el procedimiento autorizado de `lab/official_source/cutover/README.md`. No ejecutar la carga ni desplegar sólo un backend incompatible mientras el frontend productivo permanezca en el contrato anterior.

Para publicar código en una rama de preparación sin iniciar builds Netlify, utilizar el marcador de commit `[skip netlify]`. No utilizar `[skip ci]`, que impediría ejecutar GitHub Actions.
