# PRD 3A.5 — conciliación automática privada

La conciliación toma la lista privada de 427 identidades y los snapshots de
catálogo de 3A.3, más la matriz de origen y los faltantes de 3A.4. No consulta
ni escribe Supabase. Conserva los 151 grupos formados por cuenta, cadena,
formato, modelo, presentación y código comercial; si no existe código, aísla
la identidad en su propio grupo. Compartir modelo no convierte dos SKU en
duplicados.

## Criterio de verificación

Un UPC único es un **candidato**, no una correspondencia verificada por sí
solo. Para `COINCIDENCIA_VERIFICADA` se requiere simultáneamente:

1. UPC asociado a un solo producto activo del snapshot.
2. Correspondencia estructurada y acreditada de cuenta, cadena privada,
   cadena del catálogo, scope y formato.
3. Atributos acreditados del producto: modelo, presentación, color y código
   comercial, iguales tras normalizar sólo mayúsculas, acentos y puntuación.
4. Objetivo que no esté ya vinculado a otra identidad privada.

Un archivo opcional de evidencia estructurada puede contener dos arreglos:

```json
{
  "account_scopes": [{
    "account": "A", "chain": "Cadena privada", "chain_id": "UUID",
    "scope": "Código de scope", "format": "Formato privado",
    "status": "ACCREDITED", "evidence": "Referencia documental"
  }],
  "product_attributes": [{
    "product_id": "UUID", "chain_id": "UUID", "model": "Modelo",
    "presentation": "Presentación", "color": "Color",
    "commercial_code": "Código o null", "status": "ACCREDITED",
    "evidence": "Referencia documental"
  }]
}
```

Si el formato privado difiere textualmente del scope, la entrada de
`account_scopes` requiere además `"scope_equivalence": true` y evidencia
documental de esa equivalencia. El motor no inventa equivalencias. Un UPC sin
coincidencia queda `SIN_CORRESPONDENCIA`; uno con varios productos queda
`AMBIGUO`. Los demás desacuerdos o faltas de acreditación quedan
`REVISION_REQUERIDA`, con los campos concretos en `differences`.

El expediente de 3A.3 indica cero mapeos de cuenta/scope/producto aprobados,
y la matriz de 3A.4 inicia con 427 dictámenes pendientes. Por ello, con los
insumos actuales la salida correcta tiene **cero verificados**. Los 356
candidatos con formato textual compatible y los 22 con diferencia de formato
siguen pendientes de evidencia. Las 49 identidades sin UPC exacto no se
vinculan por descripción.

## Ejecución y salidas

Ejecutar `scripts/reconcile_commercial_identities.py` con `--reconciliation`
(conciliación 3A.3), `--review` (matriz de entrada JSON 3A.4), `--current`
(productos vigentes), `--missing` (49 faltantes) y `--out` (directorio privado
nuevo). `--accreditation` es opcional. La ruta de salida debe ser nueva; el
script nunca sobrescribe versiones. No hay bandera de carga productiva.

Las salidas son `correspondencias_verificadas.json`,
`matriz_correspondencias.json`, `matriz_cadena_modelo_color.csv`,
`excepciones.json`, `excepciones.csv` e `integridad_cobertura.json`.
Los JSON registran SHA-256 de cada entrada. Las sugerencias por descripción
del expediente 3A.4 quedan marcadas como investigación, nunca como vínculos.
La excepción no detiene las demás filas. Inventario sin fuente se representa
como `SIN_DATO`; una identidad pendiente queda `PENDIENTE_DE_VINCULACION`.

Una futura carga operativa necesitaría acreditar la evidencia documental,
resolver excepciones e implementar autorizaciones y control transaccional por
separado. Este PRD no añade pantallas, modifica motores, WAPE, Champions,
V01–V03, SQL ni datos productivos.
