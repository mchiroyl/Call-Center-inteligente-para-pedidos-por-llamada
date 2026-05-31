# Documentacion del proyecto

## Nombre

Call Center Inteligente para Pedidos por Llamada.

## Descripcion del problema

Los negocios que reciben pedidos por telefono pueden perder informacion importante cuando el proceso depende solo de una persona tomando notas. Los errores mas comunes son pedidos incompletos, direcciones incorrectas, falta de confirmacion, poca visibilidad para cocina/caja/operaciones y ausencia de seguimiento para el cliente.

## Objetivo del sistema

Implementar un sistema web funcional que permita recibir pedidos por llamada simulada o llamada telefonica con Twilio, transcribir la voz, interpretar la intencion del cliente con IA, estructurar el pedido, confirmarlo, guardarlo en una base de datos local y mostrarlo en paneles operativos.

## Funcionalidades principales

- Interfaz web para llamada simulada desde navegador.
- Integracion opcional con Twilio para llamadas reales.
- Webhooks para voz, estado de llamada y mensajes.
- Transcripcion de audio con AssemblyAI.
- Respuesta hablada con TTS.
- Workflow con LangGraph para manejar estado conversacional.
- RAG sobre menu y politicas del negocio.
- Extraccion estructurada del pedido.
- Confirmacion del cliente antes de guardar la orden.
- Persistencia en SQLite.
- Dashboard operativo para cocina, caja y operaciones.
- Seguimiento publico del pedido por codigo.
- Autenticacion por roles demo.

## Tecnologias utilizadas

- Frontend: Svelte 5 y Vite.
- Backend: FastAPI.
- Comunicacion en tiempo real: WebSocket.
- Telefonia: Twilio Media Streams.
- Tunel local para pruebas: ngrok.
- STT: AssemblyAI.
- TTS: Cartesia u OpenAI, segun configuracion.
- IA y structured output: OpenAI.
- Workflow: LangGraph.
- RAG y embeddings: indice semantico local sobre SQLite.
- Base de datos: SQLite.
- Gestor Python: uv.
- Gestor frontend: pnpm mediante Corepack.

## Flujo general

1. El cliente entra por llamada simulada web o llamada real de Twilio.
2. El backend recibe la conexion por WebSocket.
3. El audio se procesa y se transcribe a texto.
4. LangGraph mantiene el estado de la conversacion.
5. El sistema consulta contexto del menu y politicas mediante RAG.
6. El modelo genera salida estructurada del pedido.
7. Si falta informacion, el sistema pide aclaracion.
8. Si el pedido esta completo, solicita confirmacion.
9. Al confirmar, guarda la orden en SQLite.
10. El dashboard operativo muestra la orden en tiempo real.
11. El cliente puede consultar el estado con su codigo de seguimiento.
12. La llamada se cierra de forma controlada.

## Decisiones tecnicas importantes

- FastAPI centraliza API HTTP, WebSocket y webhooks.
- LangGraph separa el flujo en nodos y conserva estado por sesion.
- SQLite se usa para que el proyecto sea facil de clonar y ejecutar localmente.
- Las bases de datos runtime se crean automaticamente al iniciar el backend.
- Las credenciales se cargan por variables de entorno y no deben subirse al repositorio.
- El frontend compilado se sirve desde el backend para simplificar la ejecucion.
- Twilio y ngrok se dejan como integracion configurable, no como requisito obligatorio para usar la demo web.

## Bases de datos

El sistema crea sus bases de datos automaticamente durante el primer arranque del backend.

- `data/call_center.db`: datos principales del sistema, menu, sesiones, ordenes y eventos.
- `data/langgraph_checkpoints.db`: checkpoints de LangGraph para estado conversacional.
- `data/twilio-runtime.db`: base runtime usada por el modo Twilio cuando se configura asi en los scripts.

Estas bases runtime no deben subirse al repositorio porque se regeneran localmente y pueden contener datos de prueba.

## Variables de entorno requeridas

- `OPENAI_API_KEY`: requerida para IA, embeddings y structured output.
- `ASSEMBLYAI_API_KEY`: requerida para transcripcion de voz.
- `CARTESIA_API_KEY`: requerida si se usa Cartesia para TTS.

## Variables de entorno opcionales para Twilio

- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_PHONE_NUMBER`

## Limitaciones actuales

- La URL publica de ngrok puede cambiar si no se usa dominio fijo.
- SQLite es adecuado para demo y desarrollo local, pero no para alta concurrencia en produccion.
- Las cuentas demo deben cambiarse o reemplazarse antes de un despliegue real.
- La funcionalidad de SMS en numeros de Estados Unidos puede requerir registro A2P 10DLC.
- El sistema depende de servicios externos para IA, STT, TTS y telefonia.
- No se debe usar para emergencias ni decisiones criticas.

## Posibles mejoras futuras

- Despliegue en servidor HTTPS estable.
- Base de datos administrada como PostgreSQL.
- Autenticacion robusta con usuarios reales y politicas de permisos.
- Observabilidad con metricas, trazas y alertas.
- CI/CD con pruebas automaticas.
- Panel analitico de tiempos, ventas y productos mas pedidos.
- Manejo multi-sucursal.
- Mejoras de privacidad, retencion y auditoria de datos.

