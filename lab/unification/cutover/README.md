# Propuesta de corte reversible RS

Estos archivos quedan fuera de `supabase/migrations`. El generador emite SQL y no se conecta a PostgreSQL. El cargador Python exige una base sintética marcada en loopback. La preparación de código, la autorización de respaldo real y el corte hospedado son acciones separadas.

## Artefactos verificables

- `python -B -m services.operational_versions.cutover_sql before-a` genera el contrato de observaciones, manifiestos, membresía y puntero. Los bloques se reutilizan de Gate B. La alternativa de precisión ensayada es `numeric(31,16)`; comprobar dependencias y compatibilidad en la restauración real antes de aprobar su aplicación.
- `python -B -m services.operational_versions.cutover_sql after-a` agrega inventario, lecturas fijadas, auditoría, trazabilidad por celda y restricciones de acceso. Ambas fases y la inicialización de A deben ejecutarse en una sola transacción de mantenimiento, con error inmediato ante cualquier condición incumplida.
- `python -B -m services.operational_versions.cutover_archive RUTA_PRIVADA --manifest-sha256 SHA256_APROBADO` verifica cada artefacto, versiones, cantidades, claves e inventario sin abrir una conexión. El SHA se obtiene del expediente aprobado, de forma independiente de la copia a importar.
- `GATEB_CONFIG` apunta únicamente al laboratorio sintético marcado. Con la configuración verificada, `python -B -m unittest services.operational_versions.test_cutover -v` ejecuta ambas fases, comprueba el cargador y ensaya la reversión.

## Prerrequisitos del operador

1. Autorizar proyecto, cuenta operadora, manifiesto, RPO/RTO y ventana. Verificar por separado el proyecto de Supabase, el endpoint y su certificado TLS. El nombre SQL `postgres` y los parámetros de mantenimiento pueden coincidir entre proyectos y no acreditan la identidad del servidor.
2. Obtener una instantánea de lectura consistente con todas las filas físicas, estados de lotes, evidencias, catálogo, restricciones, permisos y resultados protegidos. Verificar tamaños y cifrado, con custodia privada fuera de directorios sincronizados. Restaurar en una instancia independiente aislada y contrastar filas, hashes y C1. Inventariar aparte el alcance de Auth y Storage.
3. Ensayar con el esquema completo y la candidata aprobada. Confirmar la precisión de los consumidores, perfiles de importación habilitados por cadena, duración de bloqueos, inventario, Fill Rate por pares Pedido–Entrega y recuperación. Validar las ACL y las rutas heredadas en el entorno hospedado antes de una ventana real.
4. Congelar importaciones, Capture, LIVE y cambios del catálogo. Configurar la sesión propietaria con `towell.cutover_database`, `towell.cutover_manifest_sha256` y el modo explícito `REVIEWED_MAINTENANCE`. El modo `SYNTHETIC_REVIEW` exige la marca y el loopback del laboratorio. Un rol de API no puede activar versiones. El procedimiento hospedado necesita una revisión y autorización operativa independientes.

## Secuencia y reversión

En una transacción ejecutar fase 01, cotejar la selección importada completa con A y sellar su versión antes de la fase 02. Esta última vuelve a cotejar la membresía y las huellas de catálogo y observaciones físicas, captura el histórico legítimo y cierra el acceso directo a observaciones y las rutas heredadas de escritura/cierre. A es la única versión activa en este punto.

Las consultas RPC conservan ID de versión, sello y generación durante la lectura o exportación. Auditoría conserva versiones legítimamente publicadas antes de la generación fijada. Una lectura temporal selecciona sólo observaciones de la ascendencia de la versión y del histórico importado de A, y aplica la fecha acreditada antes de escoger la versión por clave. Las filas nuevas con disponibilidad desconocida siguen con `available_at` vacío. El Fill Rate devuelve numerador, denominador y cobertura de productos emparejados.

Mantener B fuera de la base hasta la transacción aprobada de importación. Tomar el bloqueo del puntero, comprobar A y la generación esperada, cotejar el paquete y su relación UPC–cadena, crear lotes con las transiciones vigentes, insertar IDs y valores de B, conservar referencias y literales de origen, vincular la membresía, sellar y activar. Una diferencia con la versión física más alta cancela la transacción. Una repetición con contexto anterior devuelve conflicto sin añadir filas. Si el resultado de la primera solicitud es incierto, consultar el evento y el puntero antes de reintentar.

Si falla la validación posterior, conservar B y volver a fijar A con su sello y la generación vigente. Verificar las mismas observaciones, cantidades, ausencias, inventario y permisos. Mantener los lectores fijados por versión al revertir. La recuperación física corresponde al respaldo restaurado cuando el puntero, el catálogo o los datos protegidos presentan daños.
