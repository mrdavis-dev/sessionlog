"""Lógica de sesiones usada por el servidor MCP."""
import json
from datetime import datetime, timedelta, timezone

from common import AREAS, from_iso, get_connection, get_tz, local_day_start_utc, to_iso

NO_AREA = "sin-area"


def save_session(area, summary, project="general", decisions=(), pending=(), tags=()):
    """Inserta una sesión y devuelve su id. Lanza ValueError si los datos no son válidos."""
    if area not in AREAS:
        raise ValueError(f"area debe ser una de: {', '.join(AREAS)}")
    summary = (summary or "").strip()
    if not summary:
        raise ValueError("el resumen está vacío")
    conn = get_connection()
    with conn:
        cur = conn.execute(
            "INSERT INTO sessions (created_at, area, project, summary, decisions, pending, tags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                to_iso(datetime.now(timezone.utc)),
                area,
                project or "general",
                summary,
                json.dumps(list(decisions), ensure_ascii=False),
                json.dumps(list(pending), ensure_ascii=False),
                json.dumps(list(tags), ensure_ascii=False),
            ),
        )
    return cur.lastrowid


def resolve_range(date=None, date_from=None, date_to=None, last=None):
    """Convierte fechas locales (YYYY-MM-DD) a un rango UTC [start, end)."""
    if date:
        start = local_day_start_utc(date)
        return start, start + timedelta(days=1)
    if last:
        today = datetime.now(get_tz()).strftime("%Y-%m-%d")
        end = local_day_start_utc(today) + timedelta(days=1)
        return end - timedelta(days=last), end
    if date_from or date_to:
        start = local_day_start_utc(date_from) if date_from else datetime(1970, 1, 1, tzinfo=timezone.utc)
        end = (local_day_start_utc(date_to) + timedelta(days=1)) if date_to else datetime.now(timezone.utc) + timedelta(days=1)
        return start, end
    raise ValueError("indica date, from/to o last")


def query_sessions(start, end, project=None, tag=None, area=None):
    """Sesiones en [start, end) con filtros combinables. area admite NO_AREA."""
    sql = "SELECT * FROM sessions WHERE created_at >= ? AND created_at < ?"
    params = [to_iso(start), to_iso(end)]
    if project:
        sql += " AND project = ?"
        params.append(project)
    if tag:
        sql += " AND EXISTS (SELECT 1 FROM json_each(sessions.tags) WHERE value = ?)"
        params.append(tag)
    if area == NO_AREA:
        sql += " AND area IS NULL"
    elif area:
        if area not in AREAS:
            raise ValueError(f"area debe ser una de: {', '.join(AREAS + (NO_AREA,))}")
        sql += " AND area = ?"
        params.append(area)
    sql += " ORDER BY created_at"

    tz = get_tz()
    return [
        {
            "id": r["id"],
            "created_at": from_iso(r["created_at"]).astimezone(tz),
            "area": r["area"],
            "project": r["project"],
            "summary": r["summary"],
            "decisions": json.loads(r["decisions"]),
            "pending": json.loads(r["pending"]),
            "tags": json.loads(r["tags"]),
        }
        for r in get_connection().execute(sql, params).fetchall()
    ]


def group_by_project(docs):
    """{proyecto: {"sessions": [...], "pending": [...]}} con pendientes sin duplicar."""
    groups = {}
    for d in docs:
        g = groups.setdefault(d["project"], {"sessions": [], "pending": []})
        g["sessions"].append(d)
        for item in d["pending"]:
            if item not in g["pending"]:
                g["pending"].append(item)
    return dict(sorted(groups.items()))


def to_json(docs, summary_by=None):
    docs = [dict(d, created_at=d["created_at"].isoformat()) for d in docs]
    out = group_by_project(docs) if summary_by else docs
    return json.dumps(out, ensure_ascii=False, indent=2)


def format_sessions(docs):
    lines = []
    for d in docs:
        lines.append(f"## {d['created_at']:%Y-%m-%d %H:%M} · {d['project']} · {d['area'] or NO_AREA}")
        lines.append(d["summary"])
        for label, key in (("Decisiones", "decisions"), ("Pendientes", "pending")):
            if d[key]:
                lines.append(f"{label}:")
                lines += [f"  - {item}" for item in d[key]]
        if d["tags"]:
            lines.append("Tags: " + ", ".join(d["tags"]))
        lines.append("")
    return "\n".join(lines)


def format_by_project(docs):
    groups = group_by_project(docs)
    lines = []
    for project, g in groups.items():
        areas = sorted({d["area"] or NO_AREA for d in g["sessions"]})
        n = len(g["sessions"])
        lines.append(f"# {project} ({n} {'sesión' if n == 1 else 'sesiones'} · {', '.join(areas)})")
        for d in g["sessions"]:
            summary = d["summary"].replace("\n", "\n  ")
            lines.append(f"- {d['created_at']:%Y-%m-%d}: {summary}")
            lines += [f"    Decisión: {item}" for item in d["decisions"]]
        lines.append("")
    pending = [(p, item) for p, g in groups.items() for item in g["pending"]]
    lines.append("# Pendientes")
    if not pending:
        lines.append("Ninguno.")
    lines += [f"- [{project}] {item}" for project, item in pending]
    return "\n".join(lines)


def format_results(docs, summary_by=None, as_json=False):
    if as_json:
        return to_json(docs, summary_by)
    if not docs:
        return "Sin resúmenes en ese periodo."
    return format_by_project(docs) if summary_by else format_sessions(docs)


def sessions_without_area():
    rows = get_connection().execute(
        "SELECT id, created_at, project, summary FROM sessions WHERE area IS NULL ORDER BY created_at"
    ).fetchall()
    if not rows:
        return "Todas las sesiones tienen área."
    tz = get_tz()
    return "\n".join(
        f"{r['id']:>4}  {from_iso(r['created_at']).astimezone(tz):%Y-%m-%d}  {r['project']}  {r['summary'].splitlines()[0][:80]}"
        for r in rows
    )


def set_area(area, ids=(), project=None, force=False):
    """Asigna el área por ids y/o proyecto. Sin force solo toca filas sin área. Devuelve filas cambiadas."""
    if area not in AREAS:
        raise ValueError(f"area debe ser una de: {', '.join(AREAS)}")
    if not ids and not project:
        raise ValueError("indica ids y/o project")
    conditions, params = [], [area]
    if ids:
        conditions.append(f"id IN ({','.join('?' * len(ids))})")
        params += list(ids)
    if project:
        conditions.append("project = ?")
        params.append(project)
    if not force:
        conditions.append("area IS NULL")
    conn = get_connection()
    with conn:
        cur = conn.execute(f"UPDATE sessions SET area = ? WHERE {' AND '.join(conditions)}", params)
    return cur.rowcount


def update_session(session_id, **fields):
    """Edita campos de una sesión (area, project, summary, decisions, pending, tags). False si no existe."""
    sets, params = [], []
    for key, value in fields.items():
        if key == "area" and value not in AREAS:
            raise ValueError(f"area debe ser una de: {', '.join(AREAS)}")
        if key == "summary" and not (value or "").strip():
            raise ValueError("el resumen está vacío")
        if key in ("decisions", "pending", "tags"):
            value = json.dumps(list(value), ensure_ascii=False)
        elif key not in ("area", "project", "summary"):
            raise ValueError(f"campo no editable: {key}")
        sets.append(f"{key} = ?")
        params.append(value.strip() if key == "summary" else value or ("general" if key == "project" else value))
    if not sets:
        raise ValueError("nada que actualizar")
    conn = get_connection()
    with conn:
        cur = conn.execute(f"UPDATE sessions SET {', '.join(sets)} WHERE id = ?", params + [session_id])
    return cur.rowcount > 0


def delete_session(session_id):
    conn = get_connection()
    with conn:
        return conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,)).rowcount > 0
