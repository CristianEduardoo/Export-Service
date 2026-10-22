# Export Service

Servicio que consulta una API externa de pedidos, valida y transforma los datos, genera un CSV, lo envía por email y
registra el resultado de cada ejecución. Se puede lanzar a mano (API HTTP) o de forma programada (scheduler).

> Solución al [Backend Integration Technical Challenge](docs/CHALLENGE.md). Los contratos de origen están en
> [`contracts/`](contracts/) y la API de origen simulada en [`examples/source-api/`](examples/source-api/).

---

## 1. Arranque rápido

**Requisitos:** Docker Engine o Docker Desktop con Docker Compose v2.

```bash
cp .env.example .env
docker compose up --build -d
docker compose ps          # todo "healthy"; migrate termina con "exited (0)"
```

| Servicio | URL |
|---|---|
| API (documentación interactiva) | http://localhost:8000/docs |
| Mailpit (emails enviados) | http://localhost:8025 |
| API de origen simulada | http://localhost:8090/docs |

La API de origen arranca con [`tests/fixtures/source_orders_casuisticas.json`](tests/fixtures/source_orders_casuisticas.json):
13 pedidos con casos válidos, duplicados e inválidos (los mismos datos que usan los tests).

Para parar: `docker compose down` (añade `-v` para borrar también la base de datos y los ficheros).

## 2. Probarlo

```bash
# Lanzar una ejecución manual → 202 Accepted con su id y estado "pending"
curl -s -X POST localhost:8000/executions | jq

# Consultar el resultado (13 leídos, 4 exportados, 7 inválidos, 2 duplicados, email enviado)
curl -s localhost:8000/executions | jq '.items[0]'
curl -s localhost:8000/executions/<id> | jq

# El fichero generado (y el email con el adjunto en http://localhost:8025)
docker compose exec worker ls -l /app/exports

# Simular un error temporal de la API de origen → 3 reintentos y "failed" con source_unavailable
curl -s -X POST localhost:8000/executions -H 'Content-Type: application/json' -d '{"scenario": "temporary_error"}'

# Reprocesar desde una fecha (ignora la marca de agua)
curl -s -X POST localhost:8000/executions -H 'Content-Type: application/json' \
  -d '{"updated_since": "2026-10-01T00:00:00Z"}'

# Dos ejecuciones seguidas → la segunda recibe 409 con el id de la que está en curso
curl -s -X POST localhost:8000/executions; curl -s -X POST localhost:8000/executions
```

**Simular un timeout:** el escenario `slow` tarda 4 s por página. Con `SOURCE_API_TIMEOUT_SECONDS=2` en el `.env`,
`docker compose up -d worker` y `{"scenario": "slow"}`, se ven los reintentos por timeout.

El cuerpo del `POST` es opcional. Campos: `trigger` (`manual` | `scheduled`), `scenario` (solo pruebas:
`temporary_error` | `slow`) y `updated_since` (reproceso). Un campo desconocido o una fecha sin zona horaria → 422.

## 3. Arquitectura

```
  curl / /docs                         Ofelia (cada SCHEDULE)
      │ POST /executions                   │ python -m app.worker --enqueue
      ▼                                    ▼
  ┌────────┐   registra "pending"   ┌──────────────┐
  │  API   │ ─────────────────────► │  PostgreSQL  │  estado, paso, contadores, reserva (lease)
  └────────┘                        └──────┬───────┘
      ▲  GET /executions                   │ reclama la siguiente (SKIP LOCKED)
      └──────────────────────────────┐     ▼
                                     │ ┌──────────┐ ─► API de origen (paginada, reintentos)
                                     └─│  WORKER  │ ─► CSV en /app/exports
                                       └──────────┘ ─► SMTP (Mailpit en local)
```

API y worker no se llaman entre sí: se coordinan a través de la base de datos. La API responde al momento (202) y el
worker hace el trabajo lento. Una misma imagen arranca la API, el worker y las migraciones con distinto comando.

**Ciclo de una ejecución** (cada paso se confirma en la base de datos antes de seguir):

```
pending → running: fetching_source → writing_file → sending_email → email_sent → completed
                   (cualquier fallo clasificado)                              → failed + error_code
```

**Código** (`app/`), por capas:

| Carpeta | Responsabilidad |
|---|---|
| `domain/` | Lógica pura (sin red ni BD): contrato y validación de pedidos, deduplicación, CSV y sha256 |
| `connectors/` | Lo externo: cliente de la API de origen, SMTP y almacenamiento de ficheros |
| `db/` | Modelo, sesiones y repositorio de ejecuciones (migraciones en `alembic/`) |
| `services/` | Casos de uso: registrar ejecuciones (API) y orquestar una ejecución (worker) |
| `api/`, `worker.py` | Puntos de entrada: solo traducen HTTP o la línea de comandos y llaman al servicio |

## 4. Decisiones

| Decisión | Motivo |
|---|---|
| **Ejecución asíncrona** (202 + worker) | Una API de origen lenta nunca bloquea la API; API y worker escalan por separado |
| **PostgreSQL** en lugar de SQLite | API y worker son procesos distintos que comparten estado; `FOR UPDATE SKIP LOCKED` permite varias réplicas del worker sin cambios. En local no tiene coste: lo levanta el compose |
| **Contrato estricto** | Se aplica `contracts/` al pie de la letra: un campo no previsto, un importe en texto o una fecha sin zona invalidan el pedido. Si la API añadiera un campo, todos los pedidos serían inválidos: se detectaría por `records_invalid` y los logs |
| **Dos niveles de validación** | Un sobre de respuesta inválido falla la ejecución (no es fiable); un pedido inválido se descarta con su motivo y el resto sigue |
| **Duplicados** | Un pedido = una fila: la versión con el `updated_at` más reciente (empate: la última recibida). Se deduplica tras juntar todas las páginas |
| **Exportación incremental** (marca de agua) | Cada ejecución pide lo que cambió desde el último `updated_at` exportado. Como la API filtra con `>=`, lo que coincide con la marca ya se envió y no se repite. Sin pedidos nuevos: `completed` sin email |
| **Email "como mucho una vez"** | Un email no es transaccional. Ante la duda no se reenvía: un duplicado silencioso es peor que un caso señalado para revisión |
| **Reintentos solo transitorios** | Timeout, conexión, 429, 502, 503 y 504, con backoff exponencial, jitter y `Retry-After`. Un 500 o un 4xx no se reintentan: repetir no los arregla |
| **Scheduler sin lógica** | Ofelia solo ejecuta `--enqueue`, que registra la ejecución por el mismo camino que `POST /executions` |
| **Importes en `Decimal`** | El JSON se lee con `parse_float=Decimal`: los importes nunca pasan por `float` |
| **CSV** (RFC 4180) | Columnas del contrato de exportación; nombre `export-<fecha UTC>-<execution_id>.csv` (único por ejecución) y sha256 |

## 5. Fiabilidad

| Si… | …el sistema |
|---|---|
| La API de origen tarda más que el timeout o responde 503 | Reintenta con esperas crecientes; si se agotan, `failed` con `source_unavailable` |
| La respuesta no cumple el contrato del sobre | `failed` con `source_contract_violation`, sin reintentar |
| Llegan pedidos inválidos o duplicados | Los descarta, los cuenta (`records_invalid`, `records_duplicated`) y sigue |
| Se piden dos ejecuciones a la vez | Un índice único parcial en PostgreSQL solo admite una activa: la otra recibe **409** |
| Hay dos workers | `SKIP LOCKED`: nunca procesan la misma ejecución |
| El worker muere a mitad | Su reserva (`lease`) caduca y la recuperación la cierra según el último paso: antes del envío → `worker_lost` (la marca no avanza y la siguiente ejecución recoge esos pedidos); **durante el envío → `email_delivery_unknown`**; después → `completed` |
| El envío del email queda en duda | No se reenvía. La ejecución queda señalada (código, log ERROR y Message-ID determinista `<execution_id@export-service>` para buscarlo en el servidor de correo) y se resuelve con un reproceso con `updated_since` |
| Se reinicia un contenedor | El estado está en PostgreSQL; ante `docker stop` el worker termina la ejecución en curso (`stop_grace_period: 60s`) |

## 6. Observabilidad

**Logs** en JSON, una línea por evento, por stdout:

```json
{"timestamp": "2026-10-21T12:29:41.213Z", "level": "INFO", "service": "worker", "logger": "...",
 "event": "execution_completed", "message": "Ejecución completada", "execution_id": "b70e7d61-...",
 "records_read": 13, "records_exported": 4, "records_invalid": 7, "records_duplicated": 2, "duration_ms": 147}
```

- `execution_id` se añade solo a todos los logs de una ejecución.
- `event` es un nombre estable para filtrar o alertar: `execution_started`, `source_page_fetched`, `source_api_retry`,
  `record_invalid`, `file_saved`, `email_sent`, `execution_completed`, `execution_failed`, `email_delivery_unknown`…
- Los fallos inesperados incluyen el stack trace; los previstos, el tipo y el mensaje del error.

```bash
docker compose logs -f worker
docker compose logs worker --no-log-prefix | jq 'select(.execution_id == "<id>")'
docker compose logs worker --no-log-prefix | jq 'select(.level == "ERROR")'
```

**Health checks:** `GET /health/live` (el proceso responde) y `GET /health/ready` (PostgreSQL responde; si no, 503).
Readiness no depende de la API de origen ni del SMTP: si caen, la API sigue registrando y consultando ejecuciones, y el
fallo queda en cada ejecución. El worker tiene su propio health check (`python -m app.worker --health`, que comprueba
que el bucle sigue activo).

## 7. Docker local

| Pregunta | Respuesta |
|---|---|
| ¿Qué imagen se construye? | `export-service:<tag>`, multi-stage sobre `python:3.12.15-slim`. La etapa final solo lleva el entorno virtual y las migraciones |
| ¿Qué usuario ejecuta el proceso? | `app` (uid 10001), sin privilegios. Comprobación: `docker compose exec api id` |
| ¿Qué volúmenes y redes se necesitan? | `postgres-data` (base de datos) y `exports` (CSV). Red por defecto del compose; se publican los puertos 8000, 8025 y 8090 |
| ¿Qué health checks existen? | API (`/health/live`), worker (`--health`), PostgreSQL (`pg_isready`), Mailpit y API de origen. `depends_on` espera a que estén sanos y a que `migrate` termine bien |
| ¿Cómo se inspeccionan logs y configuración? | `docker compose logs <servicio>`, `docker compose config` (configuración resuelta) y `docker inspect` (versión en la etiqueta `org.opencontainers.image.version`) |

Toda la configuración va por variables de entorno ([`.env.example`](.env.example), sin secretos). Se valida al
arrancar: si falta una variable o es incorrecta, el proceso no arranca y lo indica.

## 8. CI/CD y despliegue

Mi forma real de desplegar es **webhook de GitHub → Jenkins → servidor Linux con Docker Compose** ([`Jenkinsfile`](Jenkinsfile)):

```
push a main ─► webhook ─► Jenkins
                            1. Clonar
                            2. Calidad: ruff, mypy y pytest (con un PostgreSQL temporal)
                            3. Build: imagen :<sha del commit> (y :latest, solo informativa)
                            4. Push a Docker Hub
                            5. Deploy por SSH: IMAGE_TAG=<sha> en el .env → docker compose pull && up -d
```

- **Versionado:** cada imagen lleva el SHA corto del commit. `latest` no se usa para desplegar.
- **Rollback de la aplicación:** relanzar el job con el parámetro `IMAGE_TAG=<sha anterior>`. Se salta el build y se
  despliega esa imagen, que sigue en el registry.
- **Rollback de la base de datos:** las migraciones deben ser compatibles con la versión anterior (añadir antes de
  quitar). Si hiciera falta deshacer una: `docker compose run --rm migrate alembic downgrade -1`.
- **Despliegue sin caída:** `up -d` sin `down` solo recrea los contenedores cuya imagen cambió.

### Equivalencias en plataformas gestionadas

No he trabajado todavía con Portainer ni con Azure Container Apps. El servicio está preparado para ellos (imagen
única, configuración por variables de entorno, logs a stdout y health checks) y es lo siguiente que quiero aprender.
Así se trasladaría cada pieza:

| Pieza | Aquí | Portainer | Azure |
|---|---|---|---|
| Registry | Docker Hub | El mismo registry, dado de alta en Portainer | Azure Container Registry |
| Despliegue | Jenkins → SSH → compose | Stack con este compose, actualizado desde Portainer o con el webhook del stack | Container Apps: `api` y `worker` como apps |
| Versión y rollback | `IMAGE_TAG` + relanzar el job | Editar el stack con la etiqueta anterior y actualizarlo | Revisiones de Container Apps: activar la anterior |
| Scheduler | Ofelia + `--enqueue` | Igual, dentro del stack | Container Apps Job con disparador programado que ejecute `--enqueue` |
| Secretos | `.env` en el servidor, fuera del repo | Variables de entorno o secrets del stack | Key Vault referenciado como secrets |
| Base de datos | PostgreSQL en contenedor + volumen | Igual | Azure Database for PostgreSQL |
| Ficheros | Volumen `exports` | Volumen | Azure Files montado, o Blob Storage con un nuevo adaptador de almacenamiento |
| Logs | stdout → `docker compose logs` | Pestaña de logs del contenedor; o envío a Loki/ELK | Log Analytics recoge stdout; consultas por `execution_id` y alertas sobre `execution_failed` |
| Health checks | `healthcheck` del compose | Estado del contenedor en Portainer | Probes de liveness y readiness sobre `/health/live` y `/health/ready` |

## 9. Preguntas de la revisión técnica

1. **¿Si la API tarda más que el timeout?** Cuenta como fallo transitorio: reintento con backoff. Si se agotan, `failed`
   con `source_unavailable`; la marca de agua no avanza y la siguiente ejecución recoge esos pedidos.
2. **¿Si el worker muere después de enviar el email?** Si llegó a guardar `email_sent`, la recuperación la da por
   completada. Si murió en `sending_email`, no se sabe si salió: `email_delivery_unknown`, sin reenvío, para revisar por
   Message-ID y resolver con un reproceso.
3. **¿Cómo evitas dos ejecuciones simultáneas?** Índice único parcial en PostgreSQL (como mucho una `pending`/`running`):
   la segunda recibe 409. Lo garantiza la base de datos, no el código.
4. **¿Qué cambiarías para dos réplicas del worker?** Nada: `SKIP LOCKED` evita que reclamen la misma y el lease recupera
   la de una réplica caída. Para procesar en paralelo habría que repartir el trabajo (por ejemplo, por rangos de fechas)
   y relajar la regla de una ejecución activa.
5. **¿Dónde persistirías los ficheros?** En almacenamiento gestionado (Azure Blob Storage o Azure Files) con política de
   retención. Solo cambia el adaptador de almacenamiento.
6. **¿Cómo recogerías los logs?** Son JSON por stdout: en Portainer, la vista de logs o un agente hacia Loki/ELK; en
   Azure, Log Analytics, filtrando por `execution_id` o `event` y con alertas sobre `execution_failed`.
7. **¿Cómo versionarías y revertirías?** Imagen etiquetada con el SHA del commit; rollback = desplegar la etiqueta
   anterior; migraciones compatibles hacia atrás.
8. **¿Qué separarías con 10 veces más volumen?** Las páginas ya se procesan una a una, pero los pedidos válidos se
   acumulan en memoria para deduplicar y generar el CSV. Escribiría el CSV de forma incremental, deduplicaría en la base
   de datos, subiría el tamaño de página y separaría la descarga del envío.

## 10. Desarrollo local

```bash
pyenv local 3.12.15                       # misma versión que la imagen (.python-version)
python -m venv venv && source venv/bin/activate
pip install -e '.[dev]'
pre-commit install                        # ruff, mypy y comprobaciones en cada commit (requiere el venv activado)

pytest -m "not integration"               # unitarios: sin Docker
docker run --rm -d --name pg-tests -p 5433:5432 -e POSTGRES_PASSWORD=tests \
  -e POSTGRES_DB=export_service_test postgres:17
pytest                                    # todos, incluidos los de integración contra PostgreSQL
```

Los tests unitarios cubren el contrato, la deduplicación, el CSV, los reintentos de la API y del SMTP, la configuración
y los logs. Los de integración (PostgreSQL real) cubren el repositorio (concurrencia, lease, marca de agua), la máquina
de estados con sus fallos y recuperaciones, y la API. Sin PostgreSQL, en local se saltan; en el CI fallan.

## 11. Limitaciones y mejoras

**Priorización.** El enunciado orienta 4–6 horas; he dedicado un poco más, y he priorizado contratos, fiabilidad
(reintentos, concurrencia, recuperación, idempotencia del email) y tests sobre la infraestructura gestionada, que queda
documentada pero no desplegada.

- **Dependencias:** las directas están fijadas; las transitivas no. Mejora: fichero de bloqueo (pip-tools o uv).
- **Marca de agua:** un pedido que llegara más tarde con exactamente el mismo `updated_at` que la marca se perdería.
  Mejora: guardar los ids exportados o usar una ventana de solapamiento.
- **Pedidos inválidos:** se releen y registran en cada ejecución hasta que el origen los corrija.
- **Duplicados:** solo se cuentan; no se registran sus ids.
- **Email incierto:** se resuelve a mano. Con un proveedor real (Microsoft Graph, SendGrid…) se podría consultar
  automáticamente si existe el Message-ID.
- **CSV:** no se protege frente a inyección de fórmulas (valores que empiezan por `=`); el destino es un sistema interno.
- **Retención:** ni los ficheros ni las ejecuciones se purgan.
- **Scheduler:** Ofelia necesita el socket de Docker (equivale a root sobre el host; el montaje `:ro` no lo limita) y no
  tiene health check. En producción, un scheduler gestionado. Para schedulers que lanzan un contenedor por ejecución
  (Azure Container Apps Jobs) faltaría un modo que registre, procese y termine.
- **Paralelismo:** una sola ejecución activa por diseño.
