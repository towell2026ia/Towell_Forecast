# Consulta de la fuente oficial en desarrollo local

La adaptación parte de `7594a58fefdf7fcbf195c0739d615f8825ff868f`. Conserva los componentes del portal, los RPC revisados, sus políticas RLS y los motores existentes. No incorpora un motor ni una aplicación alternativa.

`LOCAL_POSTGRES_CONFIG` habilita el adaptador únicamente con `APP_ENV=development` o `test`, `API_HOST=127.0.0.1`, ausencia de identificadores Railway y `SUPABASE_URL=http://127.0.0.1:<API_PORT>`. Producción y staging rechazan esta configuración antes de leer credenciales. La dependencia PostgreSQL es opcional: `services/assistant_api/requirements-local.txt`; el Dockerfile de Railway no la instala.

La configuración privada identifica host, puerto, base `gateb_official_*`, directorio, marcador de laboratorio e identificador de clúster. Cada conexión comprueba esos valores. La contraseña local se mantiene en un archivo binario Windows DPAPI del usuario, fuera de Git. Nunca se usa una URL o clave productiva para enrutar consultas locales.

El gateway expone solamente el subconjunto de Auth/REST utilizado por el portal. Las identidades son fixtures de desarrollo, identificadas expresamente como locales: no representan Auth hospedado. Los tokens locales no se aceptan si no pertenecen a una sesión emitida por este proceso. Las consultas comerciales sí se ejecutan contra PostgreSQL real, en una transacción de sólo lectura, con `authenticated`, `auth.uid()` y las políticas RLS vigentes. Los filtros se traducen a identificadores validados y parámetros SQL. No existe un endpoint SQL general. Las escrituras REST, las funciones de publicación y las tablas históricas crudas quedan cerradas.

El backend usa ese mismo gateway en su transporte de lectura. El navegador consulta las mismas rutas mediante HTTP loopback. La excepción del frontend exige simultáneamente `NODE_ENV=development`, `NEXT_PUBLIC_LOCAL_POSTGRES_ENABLED=true` y el origen canónico `http://127.0.0.1:8000`. Ningún flag habilita HTTP remoto ni modifica los controles productivos.

## Arranque

Utilizar la instancia PostgreSQL y la configuración privadas preparadas por el operador. No ejecutar el inicializador sintético de Gate B sobre datos comerciales. La instalación y carga de ensayo reutilizan `AuthorizedOfficialConnection(local_rehearsal=True)` y los dos SQL de `lab/official_source/cutover`, en una sola transacción exterior con `autocommit=True`.

Backend, después de configurar las rutas privadas, credencial y flags de lectura:

```powershell
python -B -m uvicorn services.assistant_api.api:app --host 127.0.0.1 --port 8000
```

Frontend con el mismo Next.js usado por el despliegue Netlify, sin build remoto:

```powershell
$env:NEXT_PUBLIC_SUPABASE_URL='http://127.0.0.1:8000'
$env:NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY='sb_publishable_local_development_only'
$env:NEXT_PUBLIC_LOCAL_POSTGRES_ENABLED='true'
$env:NEXT_PUBLIC_FORECAST_API_URL='http://127.0.0.1:8000'
$env:NEXT_PUBLIC_SITE_URL='http://localhost:5173'
$env:NEXT_TELEMETRY_DISABLED='1'
& ./node_modules/.bin/next.cmd dev --hostname 127.0.0.1 --port 5173
```

El marcador `sb_publishable_local_development_only` no es una API Key de un proveedor: es configuración explícita del fixture local. Las contraseñas y sesiones locales nunca se incluyen en estas instrucciones. No se deben configurar `LOCAL_POSTGRES_CONFIG` ni los flags del navegador en Netlify o Railway.

## Límites de evidencia

- Una restauración del ensayo local acredita esa fuente y el alcance del respaldo ensayado. No acredita una recuperación integral del Supabase productivo, Storage ni Auth gestionado.
- Importar resultados previamente calculados conserva su emisión original por scope como procedencia. La fecha de importación SQL es una fecha diferente. No se certifica WAPE LIVE ni se publica un Champion.
- La investigación real mantiene la clave existente en Railway. No se utiliza una clave alternativa local. El ensayo sin acceso a esa clave debe informar OpenAI/Deep Research deshabilitados, sin sustituirlos por respuestas ficticias.
- Los comandos productivos siguen el procedimiento autorizado de `lab/official_source/cutover/README.md`. No ejecutar la carga ni desplegar sólo un backend incompatible mientras el frontend productivo permanezca en el contrato anterior.

Para publicar código en una rama de preparación sin iniciar builds Netlify, utilizar el marcador de commit `[skip netlify]`. No utilizar `[skip ci]`, que impediría ejecutar GitHub Actions.
