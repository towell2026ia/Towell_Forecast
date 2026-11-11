# Fuente comercial oficial

La propuesta usa `monthly_observations`, `import_batches`, `source_evidence` y el catálogo existente. `official_source_current` identifica los dos libros acreditados mediante una huella conjunta y una generación. Las consultas operativas leen exclusivamente los lotes de esa huella.

La generación permite detectar cambios entre páginas. Una solicitud obsoleta recibe `40001 OFFICIAL_SOURCE_CHANGED` y el cliente reinicia la consulta completa. Las consultas de auditoría aplican la misma fuente actual. Las fuentes de forecast histórico, Venta, Pedido y Entrega conservan sus métricas independientes. La lectura temporal exige disponibilidad acreditada dentro de la fuente actual.

`monthly_inventory` almacena saldos por alcance comercial, producto y mes. Conserva decimales y celdas originales. El catálogo comercial expuesto toma los atributos de la última fila documentada de cada producto y mantiene las referencias históricas. El Fill Rate utiliza pares de Pedido y Entrega con cobertura explícita.

## Instalación y carga propuestas

Los SQL se encuentran fuera de `supabase/migrations`. La primera instalación ejecuta, en una transacción, el archivo generado por CLI en `../supabase/migrations` y `02_result_source_guards.sql`, seguido por la carga oficial. Las instalaciones posteriores invocan únicamente el importador.

El operador comprueba el proyecto autorizado, el endpoint, el certificado TLS, la cuenta propietaria, la huella aprobada de `Import.json` y su generación esperada. Los mecanismos existentes de respaldo pueden conservar un artefacto de custodia privado. Este artefacto queda fuera de los lectores operativos.

El importador recibe una conexión ya autenticada. `AuthorizedOfficialConnection` comprueba el host, la base y el usuario aprobados, el proyecto `bskoyqhbgrycpwhydnnr`, TLS con `verify-full` y la cuenta propietaria. Su método `load` comparte el mismo cuerpo SQL ensayado por `OfficialSourceLab`. La clase no abre conexiones.

```python
from pathlib import Path
from services.operational_versions.official_source import (
    AuthorizedOfficialConnection, read_package,
)

payload = read_package(private_import_path, approved_import_sha256)
writer = AuthorizedOfficialConnection(
    authorized_owner_connection,
    expected_host=approved_host,
    expected_database=approved_database,
    expected_user=approved_database_user,
    project_ref="bskoyqhbgrycpwhydnnr",
    source_hash=payload["source_hash"],
    expected_generation=approved_generation,
    authorization=f"ACTIVATE_OFFICIAL_SOURCE:{payload['source_hash']}:{approved_generation}",
)
with authorized_owner_connection.transaction():
    writer.maintenance(authorized_owner_connection, payload["source_hash"])
    # Primera instalación únicamente, con las dos rutas SQL revisadas.
    for path in reviewed_installation_sql_paths:
        authorized_owner_connection.execute(Path(path).read_text(encoding="utf-8"))
    writer.load(
        authorized_owner_connection, payload, approved_import_profile_versions,
        expected_generation=approved_generation, request_id=approved_request_id,
    )
```

La transacción valida las relaciones UPC–cadena–formato, crea lotes y evidencias, importa valores con sus referencias y actualiza el registro al terminar. Un fallo revierte toda la transacción. Cada modificación efectiva avanza la generación. Los registros actuales mantienen una única identidad y las consultas no combinan fuentes retiradas.

## Retirada física posterior

`services.operational_versions.official_cleanup.retirement_plan` inspecciona las claves foráneas y genera un listado exacto de IDs, conteos, hashes y orden de eliminación. Incluye los cálculos antiguos, sus horizontes y evaluaciones, entradas de corridas, datos históricos sustituidos y las estructuras A/B existentes. Los datos actuales y las relaciones de catálogo se comprueban antes de generar el script.

`render_cleanup(plan)` produce SQL explícito, sin `CASCADE`, que requiere la autorización `DESTROY_RETIRED_SOURCE_DATA:<sha256_del_plan>`. La transacción verifica la fuente vigente y las huellas de las filas aprobadas. Cualquier cambio cancela el procedimiento. Los lotes de creación referenciados por productos se conservan como procedencia del catálogo y aparecen contabilizados en el informe. No contienen hechos consultables por las rutas de la fuente oficial.

La preparación y las pruebas usan una instancia sintética nueva. La instalación, carga oficial y retirada física productivas necesitan las autorizaciones expresas correspondientes.

## Prueba local

```text
GATEB_CONFIG=<configuración verificada del PostgreSQL sintético>
python -B -m unittest services.operational_versions.test_official_source -v
```

El ensayo cubre los cinco indicadores, saldos de meses distintos, ceros, decimales, RLS, rechazo de consultas obsoletas, sustitución atómica, conservación física previa, visibilidad de nuevos resultados y los puntos normales de creación E3/E4. El procedimiento destructivo se verifica exclusivamente sobre datos sintéticos.
