# PRD 09.2D — Inventario, reconciliación y preparación de ingesta

Estado al 25-09-2026: **bootstrap histórico BLOCKED; no se cargaron hechos ni archivos al proyecto Supabase**. El Gate 0 remoto confirmó el proyecto `bskoyqhbgrycpwhydnnr`, siete migraciones, 28 tablas `public`, RLS en las 28, cuatro buckets privados y ausencia de registros productivos inesperados. Railway permanece en SQLite; Supabase, OpenAI, Deep Research y voz continúan deshabilitados.

## Fuentes y manifiesto

El inventario se genera sin editar los archivos. El informe completo de filas/celdas/lineage queda exclusivamente en `outputs/prd09_2d/reconciliation.json`, ignorado por Git. Los hashes son SHA-256 del archivo original. **Ningún XLSX ni dato de fila se versiona.**

| Fuente | SHA-256 | Hojas | Periodos con hechos de producto extraíbles | Resultado |
|---|---|---:|---|---|
| `CADENAS FINAL 2023 (B).xlsx` | `80d208169c3c6f5ea93cf4b03517e209f4021cc27a4a5c6be400dc0cedd106ab` | 26 | Ninguno validable con perfil de actual por producto | Contiene pronósticos y agregados multianuales; no se asignan a SKU individuales. |
| `Estadistica Venta Resurtible WM 2024 Dic 2024.xlsx` | `989d2764ed53f29e1b2009e500dc460a19c2fb3c67057f2a390993f11a924b5e` | 10 | 2023-01 a 2024-12 | `BD WM 2024`, `Base` y `BD (2)`; 13,405 candidatos de métricas. |
| `Estadistica Venta Resurtible WM 2024 Dic 2024 (1).xlsx` | `c72fa3b0e6498b8bad248628517276a9b8c945f8160f05fd47384a18dbd7cc5d` | 10 | 2023-01 a 2024-12 | No es copia exacta; hay diferencias en diciembre y campos de inventario. 13,405 candidatos. |
| `Estadistica Venta Resurtible WM 2025 Febrero.xlsx` | `1554f49647e504a0acf0a83807413eed51f50d040047b8dd577cbbe770c2e0da` | 7 | 2025-01 a 2025-02 | 798 candidatos; meses posteriores en resúmenes no se tratan como cierre real. |
| `Estadistica Todas las Cadenas Cierre de Julio 2026.xlsx` | `fc11daae57969e293d305c5df673368463e3aea2b69e264c9832a17d07c001fb` | 13 | 2025-01 a 2026-07 | `BAASE` tiene 11,495 filas con fecha, cadena, ITEM, UPC y Venta/Pedido/Entrega; 34,472 candidatos válidos. |
| `Estadistica Todas las Cadenas Cierre de Julio 2026 (1).xlsx` | `fc11daae57969e293d305c5df673368463e3aea2b69e264c9832a17d07c001fb` | 13 | Igual al anterior | Copia byte a byte; detectada y excluida de la contabilización. |

Los perfiles son explícitos: en los archivos de Walmart se pasa `chain_hint`, `summary_year` y `source_cutoff`; la hoja multicanal toma cadena y fecha de cada fila. Un nombre de archivo no establece por sí solo la precedencia ni la temporalidad de disponibilidad. Los campos `Costo`, `PVP`, inventario, forecast cliente, forecast Towell, fill rate y otros quedan preservados únicamente en el XLSX privado; no se descartan de la fuente ni se incorporan como `SALES`/`ORDER`/`DELIVERY`. Requieren contrato independiente antes de promoverse a nuevas tablas.

## Reglas aplicadas

- Identidad preliminar: `(chain, UPC)` cuando el UPC está explícito; `(chain, ITEM)` sólo si no hay UPC. La asociación ITEM→UPC procede exclusivamente de un par explícito de la fuente, nunca de la descripción. Veinte ITEM tienen múltiples UPC y se bloquean para resolución. Cambio de descripción no cambia la identidad.
- Cada `(chain, product, period, metric)` se reconcilia entre todas las fuentes. Valores iguales son fuentes equivalentes y **no se suman**. Valores distintos generan un conflicto sin versión canónica. Sólo entre valores iguales se selecciona una referencia determinista por corte detectado, integridad de celda y hash; el nombre no decide.
- Fórmulas `IFERROR/XLOOKUP`, celdas vacías y errores no se convierten en cero. Los resúmenes con fórmulas se dejan fuera cuando no hay celda directa validada. Un cero explícito anterior a la primera actividad positiva queda `NOT_ACTIVE`, no `CONFIRMED_ZERO`; un producto que sólo tiene ceros queda sin activación demostrada. No se inventa una fila por producto ausente.
- Todas las observaciones canónicas candidatas quedan `availability_source=UNKNOWN`, `available_at=NULL`. Ningún XLSX aportó por sí mismo evidencia temporal E1/E2/E3 verificada. Fecha de carga actual y fecha de cierre en el nombre del archivo no se convierten en disponibilidad pasada. No se ejecuta forecast.
- Para una futura promoción `LEGACY_EVIDENCE` se exigirá evidencia E1/E2/E3 validada, hash, fecha, cadena y vínculo a la observación. El baseline SQL actual acepta `E1/E2/E3` como `availability_source`, no el literal `LEGACY_EVIDENCE`; antes de persistir con ese literal hará falta una migración contractual revisada. No se aplicó ninguna migración adicional en esta etapa bloqueada.

## Reconciliación observada

Escaneo local: 6 archivos, 13 cadenas, 914 identidades preliminares, periodos 2023-01…2026-07. Tras descartar duplicados equivalentes y conflictos, quedan **36,858** celdas candidatas: 12,389 `SALES`, 12,270 `ORDER`, 12,199 `DELIVERY`, todas temporalmente `UNKNOWN`. No son filas cargadas en Supabase ni cobertura completa del universo histórico.

| Control | Resultado |
|---|---:|
| Ceros explícitos posteriores a primera actividad | 8,509 |
| Ceros previos a primera actividad (`NOT_ACTIVE`) | 8,877 |
| Ceros sin evidencia de activación | 438 |
| Celdas faltantes | 9,412 |
| Fórmulas sin fuente directa validada | 492 |
| Valores inválidos | 13 |
| Fuentes equivalentes (sin suma) | 13,063 |
| Conflictos de valor | **730** (483 en 2024; 247 en 2025) |
| ITEM con más de un UPC | **20** |
| Cambios de descripción con identidad estable | 106 |
| Productos con primera actividad en 2023 / 2024 / 2025 / 2026 | 106 / 107 / 546 / 135 |

El libro 2023 no ofrece un perfil validado de actual por SKU; hay hechos 2023 por producto Walmart en la hoja `BD WM 2024` del corte posterior, pero **no** se infiere por ello un historial 2023 completo para las otras cadenas. Las diferencias abarcan `BD WM 2024` frente a `Base`, el corte febrero 2025 frente al cierre julio 2026 y hasta dos filas idénticamente identificadas con Pedido/Entrega distintos dentro de `BAASE`. El reporte privado contiene cada clave, valor y celda de los 730 conflictos. La regla de corte posterior no basta para resolverlos sin validar integridad y evidencia.

Control de regresión: el escáner genérico coincidió con **96/96** hechos comparables de FENDI BD 2024, para las ocho UPC conocidas tras la primera actividad, frente al export privado validado en PRD 01; cero diferencias. Los ceros previos al lanzamiento no se promovieron como hechos. FENDI sólo se usa aquí como comparación, no como filtro del motor.

## Operación mensual futura

`services/assistant_api/monthly_imports.py` define perfiles versionados de columnas, lectura CSV/XLSX, sellado de `uploaded_at` por reloj del servidor, SHA-256/idempotencia por cadena, validación, preview sin hechos, producto/categoría dinámicos por cadena, versionado append-only, `SYSTEM_INGESTION` con `available_at=uploaded_at`, y rollback probado mediante un puerto en memoria. Se rechazan fórmulas no verificadas, errores, códigos inválidos y lotes parcialmente rechazados. El archivo se identifica mediante ruta privada `{chain_id}/{sha256}/{filename}`. La prueba del puerto en memoria **no** certifica Supabase Storage ni una transacción PostgreSQL remota.

No se exponen todavía los endpoints `/api/imports/*` ni un ServiceClient al navegador. Faltan un adaptador de Storage privado, una operación transaccional PostgreSQL y auth/roles de usuario integrados antes de habilitar la ruta pública. Tampoco se alteró el backend Railway ni se hizo cutover de runtime. Estas capacidades siguen pendientes; no deben etiquetarse como `PASS` productivo.

Para repetir el escaneo, ejecutar `python scripts/scan_historical_corpus.py` con `--source FILE[::CHAIN[::YEAR[::CUTOFF]]` por fuente y `--output outputs/prd09_2d/reconciliation.json`; pasar los dos cierres de 2024 con `::Walmart::2024::2024-12`, febrero 2025 con `::Walmart::2025::2025-02` y el maestro multicanal sin pista. `scripts/check_historical_golden.py` compara el JSON con el CSV de hechos previamente validado, suministrado desde una ruta privada. Ni el corpus ni el reporte local deben añadirse a Git.

## Gate pendiente

La carga real exige resolver los 730 conflictos, los 20 alias ambiguos, verificar ceros sin actividad y completar la interpretación del libro 2023. Se requiere una decisión documentada sobre precedencia por fuente/celda o evidencia que la sustente; hasta entonces **no** se crea una versión histórica arbitraria ni se ejecuta bootstrap. Tras resolver el corpus, el siguiente cambio deberá implementar y probar el contrato SQL de alias/lineage completo, adapter de Storage y confirmación en una sola transacción con RLS/autorización antes de cualquier escritura masiva.
