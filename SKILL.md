---
name: session-log
description: Guarda resúmenes de sesiones elegidas (área trabajo o personal) y los consulta por fecha, rango, proyecto o área mediante el servidor MCP session-log. Úsala cuando el usuario pida "guardar/registrar el resumen de la sesión", "session-log", o pida "qué hicimos el día X", "resumen de la semana", "resumen del 1 al 7 de octubre", "reporte del mes", "standup".
---

# session-log

Guarda resúmenes de sesión en el servidor MCP `session-log` y los consulta por fecha.
Guarda **solo cuando el usuario lo pida**; nunca automáticamente.

Usa las herramientas MCP del servidor `session-log`: `save_session`, `query_sessions`, `list_sessions_without_area` y `set_area`. En Claude Code aparecen como `mcp__session-log__<herramienta>`.
Si no están disponibles, dile al usuario que el servidor MCP `session-log` no está configurado en este cliente. No simules haber guardado ni inventes resultados.

## Guardar un resumen

1. Redacta un resumen breve de la sesión (3–8 líneas) en el idioma del usuario: qué se hizo, resultado y estado final.
2. Extrae, si los hay, las **decisiones** tomadas y los **pendientes**.
3. Identifica el proyecto (nombre de carpeta o repo; si no es claro, `general`) y 1–3 tags.
4. Determina el **área**: `trabajo` o `personal`. Es obligatoria.
   Si el proyecto, la carpeta o lo dicho en la sesión no la dejan clara, **pregúntale al usuario antes de guardar**. Nunca la adivines.
5. No incluyas secretos, tokens ni credenciales.
6. Llama a `save_session` con `area`, `project`, `summary`, `decisions`, `pending` y `tags`.
7. Confirma al usuario con el ID devuelto.

## Consultar por fecha o rango

Usa `query_sessions` con `date`, `from`/`to` o `last`. Las fechas son `YYYY-MM-DD` en hora de Panamá.
Convierte expresiones como "ayer", "esta semana" u "octubre" usando la fecha actual.

- Filtros combinables: `project`, `tag` y `area` (`trabajo`, `personal` o `sin-area`). Si el usuario dice "del trabajo" o "personal", filtra por esa área.
- Para standups o reportes semanales o mensuales, usa `summary_by: "project"`: las sesiones salen agrupadas por proyecto y los pendientes al final.
- `format: "json"` devuelve los datos estructurados, por si necesitas procesarlos.

Presenta un resumen consolidado al usuario. Si no hay resultados, dilo claramente; no inventes contenido.

## Sesiones antiguas sin área

Las sesiones guardadas antes de existir el campo `area` no lo tienen.
Usa `list_sessions_without_area` para verlas, y `set_area` con `area` más `ids` y/o `project` para asignarla.
Solo se modifican sesiones sin área, salvo con `force: true`. Pregunta al usuario el área de cada una; no la deduzcas.
