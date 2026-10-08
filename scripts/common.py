"""Utilidades compartidas: conexión a SQLite y manejo de zona horaria."""
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"
AREAS = ("trabajo", "personal")

# area admite NULL solo por las filas anteriores a la migración; save_session la exige.
AREA_COLUMN = "area TEXT CHECK(area IN ('trabajo','personal'))"

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,              -- UTC, formato ISO: 2026-10-07T21:30:00Z
    project    TEXT NOT NULL DEFAULT 'general',
    summary    TEXT NOT NULL,
    decisions  TEXT NOT NULL DEFAULT '[]', -- JSON array
    pending    TEXT NOT NULL DEFAULT '[]', -- JSON array
    tags       TEXT NOT NULL DEFAULT '[]', -- JSON array
    {AREA_COLUMN}
);
CREATE INDEX IF NOT EXISTS idx_sessions_created_at ON sessions(created_at);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project);
"""


def migrate(conn):
    """Agrega la columna area a bases creadas antes de existir. No toca los datos."""
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)")}
    if "area" not in columns:
        with conn:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {AREA_COLUMN}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_area ON sessions(area)")


def load_env():
    """Carga .env (simple, sin dependencias). Las variables ya definidas tienen prioridad."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def get_tz():
    name = os.environ.get("SESSION_LOG_TZ", "America/Panama")
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        # Panamá no usa horario de verano: UTC-5 fijo como respaldo.
        return timezone(timedelta(hours=-5))


def get_connection():
    load_env()
    path = Path(os.path.expanduser(os.environ.get("SESSION_LOG_DB", "~/.claude/session-log.db")))
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    migrate(conn)
    return conn


def local_day_start_utc(date_str: str) -> datetime:
    """'2026-10-07' (hora local) -> inicio de ese día en UTC."""
    d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=get_tz())
    return d.astimezone(timezone.utc)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(ISO_FMT)


def from_iso(s: str) -> datetime:
    return datetime.strptime(s, ISO_FMT).replace(tzinfo=timezone.utc)
