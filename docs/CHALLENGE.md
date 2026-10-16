# Backend Integration Technical Challenge

## Contexto

Un sistema interno necesita generar periódicamente un fichero de exportación con información obtenida desde una API externa y enviarlo a un destinatario por email.

La solución debe poder ejecutarse manualmente y de forma programada. También debe permitir consultar qué ocurrió en cada ejecución sin tener que entrar en el contenedor.

## Objetivo

Diseña e implementa una solución desplegable con Docker que:

1. consulte la API externa;
2. valide y transforme la respuesta;
3. genere un CSV o JSON de exportación;
4. envíe el fichero por SMTP;
5. registre el estado y resultado de cada ejecución;
6. exponga una API para consultar y lanzar ejecuciones;
7. soporte ejecución como worker independiente;
8. proporcione logs útiles y health checks;
9. incluya CI y documentación de despliegue.

## API de origen

El entorno auxiliar incluido en `examples/source-api/` expone:

```http
GET /v1/orders?updated_since=<ISO-8601>&page=<int>&page_size=<int>
```

La respuesta sigue [`contracts/source-orders-response.json`](../contracts/source-orders-response.json).

La API puede devolver:

- registros válidos;
- registros con campos incompletos;
- datos duplicados;
- respuestas paginadas;
- HTTP 503 temporal usando `?scenario=temporary_error`;
- latencia usando `?scenario=slow`.

No se debe confiar en que todos los registros recibidos son válidos.

## API interna mínima

La aplicación debe exponer como mínimo:

```http
POST /executions
GET  /executions
GET  /executions/{execution_id}
GET  /health/live
GET  /health/ready
```

`POST /executions` debe devolver un identificador de ejecución y un estado inicial. El trabajo puede ejecutarse de forma síncrona o asíncrona, pero la decisión debe estar documentada.

Ejemplo de detalle:

```json
{
  "id": "8e7d2bb2-1dd2-4f4c-9bb7-6b8a2fd73c19",
  "status": "completed",
  "started_at": "2026-10-16T08:00:00Z",
  "finished_at": "2026-10-16T08:00:04Z",
  "records_read": 150,
  "records_exported": 148,
  "file_name": "export-2026-10-16.csv",
  "file_sha256": "optional",
  "email_sent": true,
  "error": null
}
```

Estados recomendados: `pending`, `running`, `completed`, `failed`.

## Requisitos técnicos

### Contenerización

La solución debe arrancar con una orden documentada, idealmente:

```bash
docker compose up --build
```

Debe poder ejecutarse como mínimo con servicios separados para:

- API HTTP;
- worker;
- servicio auxiliar de origen;
- SMTP local, por ejemplo Mailpit.

El scheduler puede ser Ofelia, cron, un servicio scheduler o una alternativa razonada. No se debe ocultar la lógica de negocio dentro del scheduler.

### Configuración

URL de la API externa, SMTP, destinatarios, timeouts, reintentos, frecuencia y nivel de log deben ser configurables mediante variables de entorno o un mecanismo equivalente.

No incluir secretos en el repositorio.

### Fiabilidad

Explica y, cuando sea razonable, implementa:

- timeout de llamadas externas;
- reintentos únicamente para fallos transitorios;
- backoff;
- comportamiento ante registros inválidos;
- protección frente a ejecuciones concurrentes;
- idempotencia si el proceso falla después de enviar el email;
- persistencia del estado de ejecución;
- comportamiento tras reiniciar un contenedor.

### Observabilidad

Los logs deben salir por `stdout`/`stderr` y contener, al menos:

- timestamp;
- nivel;
- servicio;
- `execution_id`;
- evento;
- duración o contadores relevantes;
- error y stack trace cuando aplique.

Añade health checks para liveness y readiness. Métricas o trazas son opcionales, pero documenta cómo recogerías los logs en Portainer o Azure.

### CI/CD

Incluye una pipeline que ejecute, como mínimo:

- tests;
- lint o chequeo equivalente;
- build de la imagen;
- publicación o simulación de publicación;
- instrucciones para desplegar una versión concreta y hacer rollback.

## Qué no se espera

- un sistema distribuido completo;
- una UI;
- autenticación empresarial;
- alta disponibilidad real;
- una integración real con un proveedor de correo;
- despliegue obligatorio en una cuenta Azure o Portainer del candidato.

## Criterios de aceptación

Se considera una entrega válida cuando otra persona puede:

1. arrancarla siguiendo el README;
2. lanzar una ejecución manual;
3. consultar su estado;
4. verificar el fichero y el email en un SMTP local;
5. simular un error de la API;
6. entender qué ocurre y dónde mirar los logs;
7. construir la imagen desde cero;
8. explicar cómo la desplegaría y revertiría.

## Preguntas para la revisión técnica

1. ¿Qué sucede si la API externa tarda más que el timeout?
2. ¿Qué ocurre si el worker muere después de enviar el email?
3. ¿Cómo evitarías dos ejecuciones simultáneas?
4. ¿Qué cambiarías para ejecutar dos réplicas del worker?
5. ¿Dónde persistirías los ficheros en producción?
6. ¿Cómo recogerías los logs en Portainer y en Azure?
7. ¿Cómo versionarías y revertirías las imágenes?
8. ¿Qué parte separarías si el volumen de datos creciera diez veces?
