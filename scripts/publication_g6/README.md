# Publicación de la corrida sellada de Pedido

La publicación consume el ZIP sellado y la exportación SQL de la misma corrida. Ambos se conservan en el expediente privado. La aplicación utiliza Supabase Auth y los permisos vigentes de cada cadena. Los datos comerciales permanecen fuera de Git, del directorio servido por Netlify y de los artefactos de CI.

## Preparación

1. Verificar independientemente los SHA-256 del ZIP y del manifiesto SQL. Ejecutar `prepare.py --archive ARCHIVO --snapshot DIRECTORIO --output NUEVO_DIRECTORIO_PRIVADO --archive-sha SHA_ZIP --manifest-sha SHA_MANIFIESTO`. El directorio debe ser nuevo. El preparador valida los miembros del expediente, el contexto, la corrida y el calendario.
2. Generar la verificación con `verify.py DIRECTORIO_DE_LOTES DIRECTORIO_DE_SNAPSHOT`. Ensayar los dos SQL numerados y los lotes en una base privada de prueba. Validar totales, inventario y cumplimiento, aislamiento por usuario y la persistencia de una decisión manual en esa base.
3. Registrar los despliegues anteriores del sitio y del servicio asociado a su dominio, las variables que cambiarán y los conteos y huellas previos. Mantener la copia de G6 y la identidad del proyecto en el expediente privado.

## Carga y publicación

1. Instalar las migraciones numeradas en orden en el proyecto autorizado. Todos los recursos nuevos quedan aislados de los lectores previos.
2. Ejecutar `stage.py --repo CHECKOUT_VINCULADO --project-ref ID --batches DIRECTORIO_PRIVADO --archive-sha SHA_ZIP --manifest-sha SHA_MANIFIESTO`. Cada comprobante identifica una transacción y su contenido. Una interrupción permite reanudar desde los comprobantes verificados. La carga inicial requiere cero decisiones locales para evitar perder una elección anterior.
3. Ejecutar el archivo privado `verify.sql`. Antes de publicar, un lector autenticado debe seguir sin ver la candidata a través de las vistas G6. Con CI correcto, ejecutar `activate.sql`, que repite las comprobaciones dentro de la transacción de publicación.
4. Usar el mismo SHA probado en el servicio Railway vinculado al dominio del portal y en el sitio existente de Netlify. En Railway fijar `HOSTED_UNIFIED_PLANNING_ENABLED=true`, Supabase como proveedor de datos y los lectores de resultados guardados. Mantener desactivados los escritores E3/E4 de publicación oficial, captura y aprendizaje LIVE, además de voz y OpenAI. El guardado manual del Champion de planeación tiene su propio contrato autenticado. Netlify compila el modo hospedado del portal con la URL HTTPS de esa API.
5. Verificar en el dominio publicado el acceso de una cuenta autorizada, los extremos H1 y H12, los tres motores, las selecciones por modelo, el selector de Champion y la descarga real del XLSX. Comparar las cantidades con el expediente local, incluyendo inventario por mes, ausencia de curvas y cobertura del cumplimiento.

## Recuperación

Ante una discrepancia, encapsular primero el cambio en el servicio afectado. Para retirar esta publicación completa, restaurar el despliegue anterior de Netlify, restaurar la configuración anterior y el despliegue registrado de Railway y ejecutar `unpublish.sql` en el proyecto vinculado. La transacción despublica únicamente la corrida identificada por ID y huella. Las tablas de origen y cualquier decisión manual ya registrada permanecen disponibles para auditoría. Verificar las versiones efectivamente servidas y conservar el informe de integridad en el expediente privado.
