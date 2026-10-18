# Evidencia local — PRD 09.2E5-02A.0

Fecha: 2026-10-18. Base revisada: main `60200c63b3dc5e3ad75aa5976b1a69a8ce6e9e56`.

| Verificación | Resultado |
| --- | --- |
| Contrato Supabase, transacción READ ONLY de metadatos | 15 tablas, 154 columnas, RLS activo; última migración 202610140015 |
| Suite Python aislada | 27 pruebas PASS |
| Volumen sintético | 2,001 productos, 12,001 observaciones; sin errores ni duplicados |
| Plantilla oficial | Ocho hojas exactas, cero registros comerciales, nueve reglas confirmadas |
| Validación de captura vacía | ERROR esperado: diez incidencias de captura; ninguna cantidad creada |
| Ejemplo sintético provisional | Cero errores, ADVERTENCIA; original sin modificar |
| Ejemplo sintético con errores | Tres errores, una fila duplicada; original sin modificar |
| Integridad del original | SHA-256 antes/después iguales en pruebas |
| Validador offline | Prueba con socket bloqueado PASS |
| Protección de publicación XLSX | Archivo vacío permitido; captura, macros y conexiones rechazadas |
| Compilación Python / Ruff E9,F63,F7,F82 | PASS |
| TypeScript | PASS después de regenerar tipos locales de Next |
| Revisión visual | Ocho hojas renderizadas e inspeccionadas; ancho del atributo de diccionario corregido |

Los seis prechequeos calculados de 07_VALIDACIONES se recalcularon con el motor de
renderizado: cadena/año sin declarar y cero códigos en productos/histórico, sin
errores de fórmula. Los indicadores integrales requieren ejecutar Python y usar
la copia de consulta opcional. No se validó interactivamente en Microsoft 365.

La suite cubre controles, formatos exactos, relaciones entre hojas, años 2024–2026,
productos nuevos y UUID opcional, ITEM/UPC numéricos, ceros iniciales, modelos nuevos
y compuestos, NFC/mayúsculas/espacios, FIORE y GRANADA con varias presentaciones,
BATA como tipo, vacíos/cero, cantidades negativas/no numéricas/precisión/fórmulas,
periodos incompletos/fuera de año, fuentes inexistentes y hashes de archivos reales
sintéticos, duplicados de producto/histórico/cliente, versiones y emisión de cliente,
equivalencias pendientes/contradictorias, integridad y copia de consulta protegida.

No se cambiaron SQL, datos, RLS, aplicación web, motores, C1, decisiones humanas,
flags ni despliegues productivos. El importador productivo no está implementado.
Los pendientes de conciliación están documentados en GUIDE_CARGA_ANUAL.md.

GitHub Actions debe verificarse sobre el SHA publicado; el acta final entregada
incluye el enlace al run y el SHA completo. Este documento registra las comprobaciones
locales anteriores al commit.
