# Unificación operativa · Fuente oficial única

La instrucción del propietario del 11 de noviembre de 2026 sustituye el corte A/B. La operación prevista utiliza una sola fuente comercial dentro del proyecto Supabase existente. Cada importación conserva procedencia y una generación de actualización, y las consultas aceptan exclusivamente la fuente vigente. La generación detecta cambios durante paginación, cálculo o exportación. No permite seleccionar un conjunto anterior.

## Fuentes y correspondencia

El paquete privado `FORECAST_Towell_Paquete_Unificacion_20261109` acredita dos libros mediante SHA-256. El preparador reutiliza los parseadores y conciliaciones comprobados, valida sus huellas y produce archivos privados con filas, celdas y valores literales.

| Origen | Campo | Uso |
| --- | --- | --- |
| Cierre, BAASE | B / C / D / H | Cadena, RS/CE, formato y UPC textual |
| Cierre, BAASE | E / F / J / K | Línea, familia/modelo, color y presentación originales |
| Cierre, BAASE | Q frente a R/S | Periodo mensual. Las contradicciones se aíslan |
| Cierre, BAASE | T | FCST_CLIENTE_HISTORY |
| Cierre, BAASE | U | FCST_TOWELL_HISTORY |
| Cierre, BAASE | V / W / X | SALES / ORDER / DELIVERY |
| Cierre, BAASE | Y | Inventario agregado por cadena, formato, UPC y mes |
| Maestro | Towell, Cliente y Pedidos K:M | Referencias históricas con semántica comercial conservada. El encabezado y nombre de hoja no bastan para promoverlas a Venta real |
| Maestro | Propuesta RS Dic Def, M/O/Q/S | Forecasts de referencia y propuesta comercial guardada |

Todas las cantidades están en piezas. Se preservan decimales, ceros y ausencias. Las cinco series se consultan independientemente. Fill Rate utiliza Entrega/Pedido de pares completos por producto y mes, con cobertura y denominador válido. El inventario representa un saldo mensual y su suma por modelo requiere el mismo mes y alcance. Los libros carecen de identificadores de tienda; la gráfica identifica esa cobertura agregada.

Para el catálogo de consulta y las categorías de entrada del motor se toma el atributo documentado en el último mes disponible de cada producto al corte solicitado. Los atributos históricos originales permanecen en la evidencia de cada fila. Se identificaron cuatro productos cuya línea cambia entre Trapo y Toalla. Esta política explícita de metadatos se aplica por igual al adaptador de la plataforma y a la ejecución privada. Sus evaluaciones conservan el carácter retrospectivo.

El histórico 2024 heredado queda fuera de la nueva fuente. Los registros K:M de 2024 permanecen como referencia privada con su procedencia. La acreditación de Venta real 2024 requerirá evidencia específica. Los nombres, UPC, identidades de origen y excepciones se conservan para conciliación.

El alcance preparado es RS. CE se conserva en un archivo separado, con sus excepciones. El preparador reutiliza las 427 identidades del maestro (356 vinculadas, 22 en revisión y 49 sin correspondencia) y la conciliación del cierre. Las 516 filas RS con incidencias estructurales se mantienen identificadas. Una excepción individual no detiene las claves válidas.

## Contrato único y componentes

- Se reutilizan `monthly_observations`, `import_batches`, `source_evidence`, catálogo y permisos por cadena. Las observaciones nuevas poseen trazabilidad de la fuente acreditada y valores numéricos exactos. La propuesta SQL permanece en `lab/official_source/`, fuera de las migraciones automáticas de producción.
- `official_source_current` contiene el hash vigente, su generación, los periodos disponibles y las dos evidencias. Los RPC `operational_read_context`, `operational_read_observations`, `operational_read_inventory`, `operational_read_catalog` y `operational_read_periods` consultan esa fuente única. Un contexto obsoleto se rechaza. No existen parámetros para consultar conjuntos anteriores.
- Las vistas heredadas también se restringen a la fuente vigente. Las rutas crudas y los contratos A/B se cierran para usuarios de aplicación. La preparación queda fuera de las consultas oficiales hasta completarse la transacción de carga.
- El inventario mensual agregado utiliza `monthly_inventory` porque el contrato POS anterior exige una tienda real. Las cantidades no se asignan a tiendas inventadas. El catálogo del asistente y los filtros leen los atributos documentados de los libros.
- Períodos, histórico, indicadores, reportes, Data API y XLSX comparten fuente y generación. El gráfico 9+3 conserva sus componentes e incorpora inventario reportado. Los snapshots de cálculo y resultados provisionales deben acreditar `source_context` compatible para mostrarse como resultados de la nueva fuente.
- El motor estadístico, ML y Ensemble se ejecutan desde los adaptadores existentes con Venta real nueva y la política vigente. Un producto insuficiente conserva su motivo y ausencia de pronóstico. El consolidado por modelo suma sus variantes por cadena, cuenta, formato, presentación y código comercial acreditado. Los resultados son provisionales hasta la decisión funcional correspondiente.
- El asistente y la investigación pública reutilizan la integración existente de OpenAI. La caché se identifica por el hash de fuente y el alcance comercial. Los roles habilitados pueden iniciar investigaciones pertinentes bajo la autorización continua del propietario. La clave se utiliza en Railway. La aceptación real hospedada queda en el procedimiento de corte.

## Procedimiento de sustitución que requiere autorización productiva

1. Fijar el SHA revisado, los hashes de ambos libros, del paquete normalizado y del SQL. Revalidar el catálogo y las relaciones UPC+cadena actuales. Un cambio comprobado se informa y se concilia antes de cargar.
2. Reservar la ventana y pausar escritores durante la sustitución. Inventariar dependencias y referencias de los datos retirados, incluidos cálculos, entradas, evaluaciones, lotes y creación de catálogo.
3. Obtener la autorización específica de cambio productivo e instalar el contrato revisado dentro del mismo proyecto. Aplicar los controles de permisos y compatibilidad de lectores antes de publicar las cantidades.
4. Cargar en una transacción los lotes, evidencias, cinco series e inventario acreditados y fijar el único registro vigente. La validación diferida impide confirmar datos preparados fuera de esa transacción. Comprobar conteos, hashes, claves, precisión, totales por cadena y FENDI.
5. Desplegar los SHAs de Railway y Netlify autorizados y verificar las rutas reales con usuarios ADMIN, EDITOR, VIEWER, otra cadena y anónimo. Comprobar lectura, filtros, cálculos, asistente, fuentes externas, gráfico, matriz y XLSX para el mismo alcance. Las consultas iniciadas antes de una actualización se rechazan cuando su generación caduca.
6. Una vez verificada la sustitución, presentar el inventario exacto de eliminación y solicitar la autorización destructiva expresa. Ejecutar la retirada física preparada solamente contra ese inventario. Las dependencias de claves foráneas deben resolverse en orden; no usar CASCADE indiscriminado ni reasignar la procedencia del catálogo.
7. Verificar que los datos y contratos anteriores ya no existen como fuentes consultables ni como alternativa operativa. Las pruebas de recuperación se limitan a recuperación técnica de la única fuente oficial. El procedimiento no contempla volver a A ni activar B.

La preparación actual termina con código, paquete privado y pruebas locales/CI. La ejecución de los pasos productivos y destructivos queda pendiente de autorización expresa. Los datos de los libros y los resultados de cálculo permanecen fuera de GitHub y de los artefactos públicos de CI.

## Evidencia y límites de aceptación

Las pruebas de PostgreSQL utilizan fixtures sintéticos en una base local marcada y prueban cinco variables, inventario, precisión, atomicidad, RLS, rechazo de contextos obsoletos y exclusión de datos heredados. La ejecución privada del motor consume únicamente las filas nuevas elegibles. El ensayo sintético no acredita el Auth hospedado, el despliegue, la facturación de OpenAI ni una eliminación productiva.

La liberación comercial requiere la autorización de corte, la revalidación hospedada y el inventario destructivo aprobado. La fuente contiene inventario agregado y excepciones comerciales que deben permanecer visibles. El historial anterior no cubre periodos o productos ausentes de los nuevos libros.
