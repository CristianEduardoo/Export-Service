# Notas de despliegue que debe cubrir la solución

Estas preguntas forman parte del README de la entrega del candidato, no requieren acceso real a infraestructura.

## Docker local

- ¿Qué imagen se construye?
- ¿Qué usuario ejecuta el proceso?
- ¿Qué volúmenes y redes se necesitan?
- ¿Qué health checks existen?
- ¿Cómo se inspeccionan logs y configuración?

## Portainer

Documentar:

1. cómo publicar la imagen en un registry;
2. qué variables se configuran como environment o secrets;
3. cómo crear o actualizar el Stack;
4. cómo comprobar health y logs;
5. cómo volver a una etiqueta anterior.

No usar `latest` como único mecanismo de versionado.

## Azure

Explicar qué cambiaría al usar una alternativa como:

- Azure Container Instances;
- Azure Container Apps;
- Azure App Service for Containers;
- Azure Container Registry;
- Azure Monitor / Log Analytics.

No es necesario implementar todas las opciones. Se valorará identificar correctamente que el scheduler, el almacenamiento, los secretos y los logs pueden tener servicios gestionados equivalentes.
