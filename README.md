# session-log

Servidor MCP y skill para Claude que guardan resúmenes de sesión en SQLite y permiten consultarlos por fecha, rango, proyecto o área.

- **Servidor MCP** (`scripts/mcp_server.py`): expone las herramientas por HTTP (servidor remoto o local) o por stdio (local, sin red).
- **Skill** (`SKILL.md`): indica a Claude cuándo y cómo usar las herramientas, por ejemplo preguntar el área si no está clara.

Solo usa la stdlib de Python 3.10+ (SQLite incluido), sin dependencias.

## Formas de ejecutarlo

| Modo | Dónde vive la base | Acceso desde | Token |
|---|---|---|---|
| [Servidor remoto](#servidor-remoto-coolify--cloudflare-tunnel) (Coolify + Cloudflare Tunnel) | volumen Docker en el servidor | cualquier dispositivo | obligatorio |
| [Servidor local por HTTP](#servidor-local-por-http) (Docker o Python) | tu máquina | esta máquina o tu red local | obligatorio fuera de localhost |
| [Local por stdio](#local-por-stdio-sin-red) | `~/.claude/session-log.db` en WSL | Claude Code y Claude Desktop de esta PC | no aplica |

Elige uno solo como fuente de verdad: cada modo usa su propia base.

Variables de entorno (ver `.env.example`):

| Variable | Por defecto | Uso |
|---|---|---|
| `MCP_AUTH_TOKEN` | — | Token Bearer. Obligatorio si el servidor escucha fuera de localhost |
| `SESSION_LOG_DB` | `~/.claude/session-log.db` (Docker: `/data/session-log.db`) | Ruta de la base |
| `SESSION_LOG_TZ` | `America/Panama` | Zona horaria de las fechas de consulta |
| `MCP_HOST` / `MCP_PORT` | `127.0.0.1` / `8000` (Docker: `0.0.0.0`) | Dirección HTTP |
| `MCP_ALLOWED_ORIGINS` | vacío | Orígenes de navegador permitidos; las peticiones con otro `Origin` se rechazan |

Genera el token con:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

## Servidor remoto (Coolify + Cloudflare Tunnel)

Despliegue en `https://session-log.mrlab.com/mcp`. Cloudflare Tunnel proxea HTTP sin problema; la base SQLite no se expone, porque vive en el volumen del contenedor.

1. En Coolify, crea un recurso desde el repositorio con el build pack **Docker Compose** (`docker-compose.yml`).
2. Define `MCP_AUTH_TOKEN` en las variables de entorno del recurso. No uses `.env` en el servidor.
3. Asigna el dominio `https://session-log.mrlab.com` al servicio `session-log`, apuntando al puerto `8000`.
4. En Cloudflare Zero Trust, agrega al túnel el hostname público `session-log.mrlab.com`, apuntando al proxy de Coolify o directamente al contenedor en el puerto `8000`, según cómo tengas configurado el túnel.
5. Verifica:
   ```bash
   curl https://session-log.mrlab.com/health
   ```

La base queda en el volumen `session-log-data` (`/data/session-log.db`), que sobrevive a redeploys.

### Migrar la base actual al servidor

Hazlo justo después del primer despliegue, antes de guardar sesiones nuevas. Primero, en WSL, crea una copia consistente de la base:

```bash
python3 -c "import sqlite3,os; sqlite3.connect(os.path.expanduser('~/.claude/session-log.db')).backup(sqlite3.connect('/tmp/session-log.db'))"
```

Luego cópiala al servidor (`scp`) y, allí, cárgala en el volumen como el usuario del contenedor:

```bash
docker exec -i $(docker ps -qf name=session-log) sh -c 'cat > /data/session-log.db' < session-log.db
```

La columna `area` se agrega sola al primer uso. Las sesiones antiguas quedan sin área; asígnala con `list_sessions_without_area` y `set_area`.

### Respaldos

Crea una copia consistente dentro del contenedor y sácala del servidor:

```bash
docker exec $(docker ps -qf name=session-log) python3 -c "import sqlite3; sqlite3.connect('/data/session-log.db').backup(sqlite3.connect('/data/backup.db'))"
docker cp $(docker ps -qf name=session-log):/data/backup.db ./session-log-$(date +%F).db
```

## Servidor local por HTTP

El mismo servidor HTTP, corriendo en tu máquina o en un equipo de tu red.

Con Docker:

```bash
MCP_AUTH_TOKEN=<token> docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build
```

O sin Docker, directamente con Python:

```bash
MCP_AUTH_TOKEN=<token> python3 scripts/mcp_server.py --http
```

Queda en `http://127.0.0.1:8000/mcp`. Desde Claude Desktop en Windows también funciona, porque WSL2 reenvía `localhost`.

Para usarlo desde otros equipos de tu red, publica en `0.0.0.0`: en `docker-compose.local.yml`, o con `--host 0.0.0.0` en Python. El token sigue siendo obligatorio, pero por HTTP plano viaja sin cifrar: úsalo solo en una red de confianza o detrás de un proxy con TLS.

## Local por stdio (sin red)

El cliente lanza el servidor como proceso, sin puerto ni token. Usa la base de WSL.

- Claude Code (WSL):
  ```bash
  claude mcp add --scope user session-log -- python3 /home/dev/session-log/scripts/mcp_server.py
  ```
- Claude Desktop (Windows), en `%APPDATA%\Claude\claude_desktop_config.json`:
  ```json
  {
    "mcpServers": {
      "session-log": {
        "command": "wsl.exe",
        "args": ["python3", "/home/dev/session-log/scripts/mcp_server.py"]
      }
    }
  }
  ```
  Si tienes varias distribuciones WSL, agrega `"-d", "<distro>"` al inicio de `args`.

La base debe estar en el filesystem de WSL: SQLite falla sobre carpetas de Windows montadas (`disk I/O error`).

## Conectar los clientes por HTTP

Usa `https://session-log.mrlab.com/mcp` (remoto) o `http://127.0.0.1:8000/mcp` (local).

- **Claude Code:**
  ```bash
  claude mcp add --transport http --scope user session-log https://session-log.mrlab.com/mcp --header "Authorization: Bearer <token>"
  ```
- **Claude Desktop:** su archivo de configuración solo lanza procesos, así que se usa el puente [`mcp-remote`](https://www.npmjs.com/package/mcp-remote), que requiere Node.js en Windows:
  ```json
  {
    "mcpServers": {
      "session-log": {
        "command": "npx",
        "args": ["-y", "mcp-remote", "https://session-log.mrlab.com/mcp", "--header", "Authorization:${AUTH_HEADER}"],
        "env": { "AUTH_HEADER": "Bearer <token>" }
      }
    }
  }
  ```
- **claude.ai (conectores personalizados):** requieren OAuth, que esta versión no implementa.

## Skill

```bash
ln -s ~/session-log ~/.claude/skills/session-log
```

En Claude Desktop o claude.ai también puedes subir solo `SKILL.md`: ya no depende de archivos locales.

## Herramientas MCP

| Herramienta | Parámetros |
|---|---|
| `save_session` | `area` (obligatoria: `trabajo` o `personal`), `summary` (obligatorio), `project`, `decisions`, `pending`, `tags` |
| `query_sessions` | `date`, `from`/`to` o `last`; filtros combinables `project`, `tag` y `area` (`sin-area` = sesiones antiguas); `summary_by: "project"`; `format: "json"` |
| `list_sessions_without_area` | — |
| `set_area` | `area` más `ids` y/o `project`; solo cambia sesiones sin área, salvo con `force: true` |

El servidor envía también instrucciones al cliente (preguntar el área, no guardar secretos), así que las reglas aplican aunque el cliente no tenga la skill.

Endpoints HTTP: `POST /mcp` (JSON-RPC, requiere `Authorization: Bearer`) y `GET /health` (sin autenticación).

## Pruebas

Usan una base temporal; nunca tocan la real.

```bash
python3 -m unittest discover -s tests -v
```

## Modelo de datos (tabla `sessions`)

| Campo | Tipo | Nota |
|---|---|---|
| id | INTEGER | autoincremental |
| created_at | TEXT | UTC, ISO 8601, indexado |
| area | TEXT | `trabajo` o `personal` (CHECK), indexado; NULL solo en filas antiguas |
| project | TEXT | indexado |
| summary | TEXT | |
| decisions / pending / tags | TEXT | arreglos JSON |

Las consultas convierten las fechas de America/Panama (UTC-5) a UTC. Al abrir una base existente, la columna `area` se agrega sola sin tocar los datos.

## Estructura

| Archivo | Rol |
|---|---|
| `scripts/common.py` | conexión, esquema, migración y zona horaria |
| `scripts/sessions.py` | lógica: guardar, consultar, formatear y asignar área |
| `scripts/mcp_server.py` | servidor MCP (stdio y HTTP) |
| `tests/test_mcp.py` | pruebas con base temporal |
| `Dockerfile`, `docker-compose.yml` | despliegue en servidor |
| `docker-compose.local.yml` | puerto publicado para servidor local |
