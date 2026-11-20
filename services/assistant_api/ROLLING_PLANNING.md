# Revisiones de Pedido: contrato preparado, sin activación hospedada

La capacidad nueva es independiente del plan G6 sellado. El valor predeterminado
de `ROLLING_PLANNING_ENABLED` es `false`; instalar código no calcula, elige ni
publica. No se aplicó SQL remoto ni se cambió un permiso productivo.

## API

Todas las rutas usan el Bearer existente y los permisos reales de perfil/cadena.
Prefijo: `/api/forecast/planning-runs`.

- `POST /`: requiere EXECUTE y `Idempotency-Key`. Cuerpo exacto:
  `source_context`, `chain_ids`, `product_ids`, `objective: "Pedido"`,
  `issue_period?: "YYYY-MM"`. El corte omitido se toma del contexto acreditado;
  uno distinto se rechaza. No admite cantidades, rutas de archivo ni actor/rol.
- `GET /`: lista únicamente corridas con productos actualmente legibles.
- `GET /{run_id}`: ficha con estado `QUEUED`, `RUNNING`, `READY` o `FAILED`,
  `created_at`, `issue_period`, `observed_cutoff`, `window_start`, `window_end`,
  `source_context`, `source_hash`, `generation` y los IDs legibles.
- `POST /{run_id}/report`: lectura, con `source_context`, `chain_ids`,
  `product_ids`. Devuelve la intersección exacta con esa corrida en `products`,
  `calculations`, `horizons`, `provisionals`, `evaluation` y `composition`.
  Un producto que no pertenece a la corrida no recibe una curva antigua.
- `POST /{run_id}/champion`: lectura con el mismo alcance y `window_start/end`.
- `POST /{run_id}/champion/promote`: requiere ADMIN, EXECUTE por cadena,
  `Idempotency-Key`, la ventana, `candidate_family`, `confirm: true` y `comment`.
  Las cantidades se toman del resultado inmutable del servidor.

Calcular no modifica Champion, recomendación ni FT-Decision. Una clave de
idempotencia repetida devuelve el evento original y no vuelve a adoptar un
Champion anterior después de una elección posterior. Cambiar el contenido con
la misma clave produce conflicto. Los punteros nuevos incluyen corrida,
fuente, ventana y producto; no escriben en los punteros por año de G6.

La fuente permanece literal. La ventana de la corrida es independiente de la
ventana del plan G6: H1 es corte+1 y H12 corte+12. Las revisiones se comparan por
mes objetivo. Diciembre comercial no se convierte en Pedido observado.

`evaluation` utiliza exclusivamente pares observados comunes por SKU, origen,
mes objetivo y horizonte. Calcula suma de errores absolutos antes de agregar;
no compensa errores de colores, no promedia WAPE y declara Bias
`OBSERVED_MINUS_FORECAST`. `observations` cuenta pares y `windows` grupos
origen/objetivo/horizonte. Un alcance incompleto, denominador cero o evidencia
duplicada no acredita un líder completo. Los IDs de comparables se filtran por
los permisos actuales del lector también al recuperar una corrida de ADMIN.

## Cold Start

Es una familia independiente `COLD_START`, nunca una copia bajo las otras tres.
Sin soporte suficiente se conserva la excepción y no se rellenan cantidades.
El resultado conserva `admission` e `incremental_effect` del motor.

La elección manual exige `accept_cold_start: true`,
`admission.manual_selection_allowed: true` y `acknowledgements` con los códigos
del motor: `COLD_START_METHOD`, `NO_TARGET_WAPE` y
`UNDETERMINED_INCREMENTAL_EFFECT`. Si el efecto es `UNDETERMINED`, sólo se admite
un SKU por operación. El evento guarda `consolidation_allowed: false` y
`admission_status: ACCEPTED_INDIVIDUAL_ONLY`; no constituye una autorización
para sumarlo al modelo. La curva individual se conserva para consulta/export.
El candidato inmutable, sus métricas y la ausencia de WAPE propio no se cambian.

## Durabilidad y recuperación

Se reutiliza SQLite y su respaldo existente: nuevas tablas de diario en el mismo
archivo autorizado. Entradas, resultados y elecciones tienen SHA-256 y triggers
que rechazan UPDATE/DELETE. Reserva idempotente, resultado/estado final y
elección/punteros usan transacciones. Los trabajos interrumpidos se marcan
`FAILED/PLANNING_INTERRUPTED`; no se guardan tokens ni se reejecutan solos.

Las pruebas sintéticas verifican otro proceso, reapertura y elecciones sin
modificar G6. Antes de activar, respaldar el archivo con el procedimiento de
`LocalPersistenceProvider.backup` y verificar SHA/integrity_check; conservar
G6 y la configuración anterior. Reversión: deshabilitar la capacidad y leer el
plan anterior; los eventos nuevos permanecen auditables. Una restauración de
respaldo es otra operación explícita, no un borrado de corridas.

## Preflight local

Usar los mismos `LOCAL_POSTGRES_CONFIG`, `STATE_DIR`, `SQLITE_PATH` y Auth ya
autorizados. Añadir sólo `ROLLING_PLANNING_ENABLED=true` al proceso del checkout
nuevo. Debe ser development/test, loopback y no Railway. El lector usa Scenario
y Observed de la política privada con sus hashes exactos, más catálogo por JWT
con estado real del producto. No se importan archivos ni se modifica la base.

## Preflight hospedado pendiente

El adaptador de entrada está preparado y probado con fixtures: lee observados
por JWT/RLS y referencias comerciales por la RPC de corrida G6 sellada. No
descarga el archivo completo, no usa service_role ni duplica referencias como
reales. Una discrepancia en observados/escenario bloquea el cálculo.

Para activarlo en el futuro se requiere autorización independiente y comprobar:

1. `PERSISTENCE_MODE=hosted-volume`, `STATE_DIR` y `SQLITE_PATH` dentro del mismo
   volumen persistente, con respaldo y prueba de recuperación de esa instalación.
2. Una única réplica/proceso escritor sobre ese volumen. Declarar
   `ROLLING_PLANNING_SINGLE_WRITER=true` no sustituye comprobar la topología.
   Un lease de archivo rechaza un segundo escritor sobre el mismo archivo;
   volúmenes distintos por réplica no proporcionan un diario único.
3. Todas las solicitudes y lecturas deben llegar al mismo volumen; no usar el
   filesystem efímero de la imagen ni escalar réplicas independientes.
4. RLS/ACL actuales por producto/cadena, cuenta real autorizada, RPC G6 disponible
   y fuentes completas/pin verificados. No cambiar roles para resolver faltantes.
5. Nueva carga mensual/cierre futuro acreditados y contrato de fuente aceptado;
   no inventar observaciones de febrero para habilitar una revisión. La prueba
   marzo–febrero de este cambio usa datos sintéticos.

No es necesaria una tabla productiva nueva para persistir revisiones: se
reutiliza el volumen existente. `scripts/publication_12m/preflight.sql` contiene
sólo consultas de preparación; no es una migración ni autoriza ejecutarla en
producción. Mantener flag OFF hasta validar estos puntos y el corte autorizado.
