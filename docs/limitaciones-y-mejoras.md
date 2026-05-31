# Limitaciones y mejoras futuras

## Limitaciones actuales

- La demo local depende de que las variables de entorno esten configuradas correctamente.
- Las llamadas reales dependen de Twilio y de que los webhooks apunten a la URL vigente de ngrok.
- La URL de ngrok puede cambiar entre ejecuciones si no se usa dominio fijo.
- SQLite funciona bien para demo local, pero no es la mejor opcion para despliegues con alta concurrencia o multiples instancias.
- Las credenciales demo incluidas en el README son solo para evaluacion academica.
- SMS con numeros de Estados Unidos puede requerir registro A2P 10DLC en Twilio.
- La calidad de transcripcion puede variar segun ruido, microfono, acento o estabilidad de red.
- El sistema depende de servicios externos: OpenAI, AssemblyAI, Cartesia y Twilio.
- No incluye monitoreo centralizado, alertas ni tablero de salud del sistema.
- No debe usarse para emergencias ni para decisiones criticas sin supervision humana.

## Mejoras futuras recomendadas

- Publicar el backend en un servidor HTTPS estable.
- Usar un dominio propio para Twilio en lugar de URL temporal de ngrok.
- Migrar persistencia a PostgreSQL para produccion.
- Implementar autenticacion real con usuarios, roles y recuperacion de contrasena.
- Cambiar credenciales demo antes de cualquier despliegue publico.
- Agregar cifrado y politicas de retencion de datos sensibles.
- Incorporar pruebas end-to-end de frontend, llamada simulada y flujo Twilio.
- Crear pipeline CI/CD para pruebas y despliegue.
- Agregar monitoreo con metricas, logs estructurados y alertas.
- Incorporar tablero de analitica de ventas, tiempos de preparacion y productos mas solicitados.
- Soportar multiples sucursales y horarios por sucursal.
- Permitir configuracion administrativa del menu sin reiniciar el sistema.
- Agregar evaluaciones automaticas de calidad de respuesta del agente.
- Mejorar recuperacion ante fallos de proveedores externos.

## Recomendacion para entrega academica

La documentacion que debe compartirse en el repositorio es esta carpeta `docs/`, junto con el `README.md`. Los entregables privados, borradores, capturas locales, logs, bases runtime y presentaciones editables que no sean necesarias para ejecutar el sistema deben mantenerse fuera del repositorio.

