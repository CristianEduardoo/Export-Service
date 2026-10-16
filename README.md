# Backend Integration Technical Challenge

Prueba técnica para evaluar la capacidad de diseñar, refactorizar y operar integraciones backend contenerizadas.

> Este repositorio es el material que se comparte con los candidatos. La evaluación interna está en `grading/` y no debe publicarse junto con el enunciado.

## Objetivo

Construir una solución que consulte una API externa, transforme sus datos en un fichero de exportación, lo envíe por email y permita consultar las ejecuciones.

La prueba evalúa principalmente:

- visión de integración;
- contratos y validación;
- separación entre API, worker y scheduler;
- Docker y Docker Compose;
- tolerancia a fallos, reintentos e idempotencia;
- logs y observabilidad;
- CI/CD y documentación de despliegue.

Python es el lenguaje esperado, pero no se puntúa como un examen de sintaxis o algoritmia.

## Enunciado para candidatos

Lee [`docs/CHALLENGE.md`](docs/CHALLENGE.md). Los contratos mínimos están en [`contracts/`](contracts/) y la API externa local en [`examples/source-api/`](examples/source-api/).

## Inicio rápido del entorno auxiliar

Requisitos:

- Docker Engine o Docker Desktop;
- Docker Compose v2;
- Git.

```bash
git clone <tu-fork-o-copia>
cd tech-back-challenge
docker compose -f examples/source-api/docker-compose.yml up --build
```

La API de origen estará disponible en `http://localhost:8090` y expone documentación OpenAPI en `http://localhost:8090/docs`.

Ejemplo:

```bash
curl 'http://localhost:8090/v1/orders?updated_since=2026-10-01T00:00:00Z&page=1&page_size=50'
```

El candidato debe crear su propia aplicación y puede incorporar este servicio en su `docker-compose.yml` mediante una ruta relativa, o utilizar un mock equivalente.

## Entregables esperados del candidato

- código de la solución;
- `Dockerfile` y `docker-compose.yml`;
- tests automatizados;
- `.env.example` sin secretos;
- pipeline CI;
- README con arquitectura, ejecución local, decisiones, observabilidad, despliegue y rollback;
- nota breve sobre limitaciones y mejoras futuras.

## Decisiones abiertas

No se impone FastAPI, SQLite, PostgreSQL, Ofelia ni una plataforma CI concreta. Se valorará que las decisiones sean explícitas, proporcionales y operables.

## Duración orientativa

4–6 horas. Si no se completa todo, se debe documentar qué se priorizó y qué quedaría pendiente.

## Material de apoyo

- [`docs/CHALLENGE.md`](docs/CHALLENGE.md): enunciado completo.
- [`contracts/`](contracts/): contratos de la API fuente y del fichero.
- [`examples/`](examples/): API mock, datos de ejemplo y configuración.
- [`docs/DEPLOYMENT-NOTES.md`](docs/DEPLOYMENT-NOTES.md): preguntas que la solución debe responder para Docker, Portainer y Azure.
