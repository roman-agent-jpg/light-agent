"""Хранение истории диалогов в Supabase.

Таблицы:
  agent_messages - переписка (роль, текст, время, метаданные)
  agent_projects - проекты: изолированные папки с файлами
  agent_facts   - память о пользователе и проектах (долговременная)
  agent_actions - журнал всех действий агента

Если Supabase не настроен - агент работает без постоянной памяти,
используя только переданную в запросе историю.
"""
import json
from datetime import datetime, timezone

from . import config

_client = None
_unavailable = False

TABLE = "agent_messages"
PROJECTS_TABLE = "agent_projects"
FACTS_TABLE = "agent_facts"
ACTIONS_TABLE = "agent_actions"


def _get_client():
    global _client, _unavailable
    if _unavailable:
        return None
    if _client is not None:
        return _client
    if not config.memory_enabled():
        _unavailable = True
        return None
    try:
        from supabase import create_client

        _client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
        return _client
    except Exception:
        _unavailable = True
        return None


def ensure_schema() -> bool:
    """Проверяет, что таблица сообщений доступна.

    Создавать её нужно один раз вручную в Supabase SQL Editor: у клиента
    нет прав на DDL, а RPC exec в Supabase не существует. Поэтому здесь
    мы только проверяем доступ и создаём таблицу, если она уже есть.
    """
    client = _get_client()
    if client is None:
        return False
    try:
        client.table(TABLE).select("id").limit(1).execute()
        return True
    except Exception:
        return False


def add_message(session_id: str, role: str, content: str,
                 meta: dict | None = None, project: str = "") -> bool:
    """Сохраняет сообщение. meta - модель, время, действия (для истории)."""
    proj = (project or config.get_current_project() or "default").strip() or "default"
    client = _get_client()
    if client is None:
        return _local_add_message(session_id, role, content, meta, proj)
    try:
        row = {
            "session_id": session_id,
            "role": role,
            "content": content[:8000],
            "project": proj,
        }
        if meta:
            row["meta"] = json.dumps(meta, ensure_ascii=False)[:2000]
        client.table(TABLE).insert(row).execute()
        return True
    except Exception:
        return _local_add_message(session_id, role, content, meta, proj)


def get_history(session_id: str, limit: int = 50, project: str | None = None) -> list[dict]:
    """Последние сообщения сессии в порядке возрастания.

    Возвращает также created_at и метаданные (модель, время),
    чтобы интерфейс мог показать переписку как она была.
    """
    client = _get_client()
    if client is None:
        return _local_get_history(session_id, limit)
    try:
        resp = (
            client.table(TABLE)
            .select("role, content, created_at, meta, project")
            .eq("session_id", session_id)
            .order("id", desc=True)
            .limit(limit)
            .execute()
        )
        rows = resp.data or []
        out = []
        for r in reversed(rows):
            meta = r.get("meta")
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = None
            out.append({
                "role": r["role"],
                "content": r["content"],
                "created_at": str(r.get("created_at", "")),
                "project": r.get("project") or "default",
                "meta": meta if isinstance(meta, dict) else None,
            })
        return out
    except Exception:
        return _local_get_history(session_id, limit)


def get_project_history(project: str, limit: int = 100) -> list[dict]:
    """Вся переписка по выбранному проекту (папке).

    Гарантирует, что при выборе любой папки сразу видна её полная история.
    """
    proj = (project or "default").strip() or "default"
    client = _get_client()
    if client is None:
        data = _read_local()
        filtered = [m for m in data if m.get("project") == proj]
        return filtered[-limit:]
    try:
        resp = (
            client.table(TABLE)
            .select("session_id, role, content, created_at, meta, project")
            .eq("project", proj)
            .order("id", desc=True)
            .limit(limit)
            .execute()
        )
        rows = resp.data or []
        out = []
        for r in reversed(rows):
            meta = r.get("meta")
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = None
            out.append({
                "session_id": r.get("session_id") or f"proj-{proj}",
                "role": r["role"],
                "content": r["content"],
                "created_at": str(r.get("created_at", "")),
                "project": r.get("project") or proj,
                "meta": meta if isinstance(meta, dict) else None,
            })
        return out
    except Exception:
        data = _read_local()
        filtered = [m for m in data if m.get("project") == proj]
        return filtered[-limit:]


def clear_session(session_id: str) -> bool:
    client = _get_client()
    if client is None:
        return _local_clear_session(session_id)
    try:
        client.table(TABLE).delete().eq("session_id", session_id).execute()
        _local_clear_session(session_id)
        return True
    except Exception:
        return _local_clear_session(session_id)


def delete_session(session_id: str) -> bool:
    return clear_session(session_id)


def status() -> dict:
    return {
        "enabled": _get_client() is not None,
        "service": "supabase",
    }


def list_sessions(limit: int = 50, project: str | None = None) -> list[dict]:
    """Сессии с названием и датой для отображения на любом устройстве.

    Если указан project, фильтрует сессии по проекту.
    """
    client = _get_client()
    if client is None:
        return _local_list_sessions(limit, project)

    try:
        q = (client.table(TABLE)
             .select("session_id, created_at, role, content, project")
             .order("id", desc=True)
             .limit(600))
        if project:
            q = q.eq("project", project)
        resp = q.execute()
    except Exception:
        return _local_list_sessions(limit, project)

    sessions: dict[str, dict] = {}
    for row in (resp.data or []):
        sid = row.get("session_id")
        if not sid:
            continue
        if sid not in sessions:
            sessions[sid] = {
                "id": sid,
                "project": row.get("project") or "default",
                "updated_at": row.get("created_at") or "",
                "title": "",
                "message_count": 0,
            }
        sessions[sid]["message_count"] += 1
        if not sessions[sid]["title"] and row.get("role") == "user" and row.get("content"):
            title = row["content"].strip().replace("\n", " ")
            if len(title) > 36:
                title = title[:35] + "…"
            sessions[sid]["title"] = title

    out = []
    for sid, data in sessions.items():
        if not data["title"]:
            data["title"] = sid
        out.append(data)
    out.sort(key=lambda x: x["updated_at"], reverse=True)
    return out[:limit]


def _read_local() -> list[dict]:
    try:
        f = config.WORKSPACE / ".memory_cache.json"
        if f.is_file():
            return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def _write_local(data: list[dict]) -> None:
    try:
        f = config.WORKSPACE / ".memory_cache.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _local_add_message(session_id: str, role: str, content: str,
                       meta: dict | None = None, project: str = "default") -> bool:
    data = _read_local()
    data.append({
        "session_id": session_id,
        "role": role,
        "content": content[:8000],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project": project or "default",
        "meta": meta,
    })
    _write_local(data[-1000:])
    return True


def _local_get_history(session_id: str, limit: int = 50) -> list[dict]:
    data = _read_local()
    filtered = [m for m in data if m.get("session_id") == session_id]
    return filtered[-limit:]


def _local_clear_session(session_id: str) -> bool:
    data = _read_local()
    data = [m for m in data if m.get("session_id") != session_id]
    _write_local(data)
    return True


def _local_list_sessions(limit: int = 50, project: str | None = None) -> list[dict]:
    data = _read_local()
    if project:
        data = [m for m in data if m.get("project") == project]
    sessions: dict[str, dict] = {}
    for m in reversed(data):
        sid = m.get("session_id")
        if not sid:
            continue
        if sid not in sessions:
            title = ""
            if m.get("role") == "user":
                title = m.get("content", "").strip().replace("\n", " ")[:35]
            sessions[sid] = {
                "id": sid,
                "project": m.get("project", "default"),
                "updated_at": m.get("created_at", ""),
                "title": title or sid,
                "message_count": 0,
            }
        sessions[sid]["message_count"] += 1
    out = list(sessions.values())
    out.sort(key=lambda x: x["updated_at"], reverse=True)
    return out[:limit]


# ---------------------------------------------------------------------------
# Сжатие истории
# ---------------------------------------------------------------------------
# Зачем: история растёт бесконечно, и каждый запрос начинает платить за всю
# переписку. Обрезать нельзя - агент забудет важное. Поэтому старые сообщения
# не удаляются, а сворачиваются в короткий пересказ: что просил пользователь
# и что получилось. Свежие сообщения остаются дословно.

RECENT_MESSAGES = 12     # сколько последних сообщений идёт дословно
MAX_SUMMARY_CHARS = 2600  # предел длины сводки
MAX_FACT_CHARS = 220     # предел одной строки в сводке


def _condense(text: str, limit: int = MAX_FACT_CHARS) -> str:
    """Одна строка-пересказ: первая фраза плюс длина."""
    clean = " ".join((text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[:limit].rsplit(" ", 1)[0] + "..."


def _build_summary(old: list[dict]) -> str:
    """Собирает сводку по сообщениям, которые не попали в окно свежих.

    Ответы агента вида "OK" и "18" бесполезны и только тратят место,
    поэтому они выбрасываются. Важны запросы пользователя и содержательные
    ответы - именно в них факты.
    """
    facts: list[str] = []
    for m in old:
        text = m.get("content", "")
        # Ответы агента почти всегда короткие служебные - не факт
        if m.get("role") != "user" and len(text.strip()) < 40:
            continue
        role = "Пользователь" if m.get("role") == "user" else "Агент"
        line = _condense(text)
        if line:
            facts.append(f"- {role}: {line}")

    if not facts:
        return ""

    summary = ("Пользователь ранее говорил (сжато). Используй эти факты, "
               "когда он про них спрашивает:\n") + "\n".join(facts)
    if len(summary) > MAX_SUMMARY_CHARS:
        summary = summary[:MAX_SUMMARY_CHARS].rsplit("\n", 1)[0] + "\n- ..."
    return summary


def build_context(session_id: str, user_message: str,
                  recent: int = RECENT_MESSAGES, project: str | None = None) -> list[dict]:
    """Готовит сообщения для LLM: сводка + свежие сообщения + новый вопрос.

    Возвращает список ровно в формате OpenAI Chat Completions.
    """
    # Забираем с запасом: часть пойдёт в сводку, часть - дословно.
    stored = get_history(session_id, limit=recent * 4, project=project)

    messages: list[dict] = []
    if len(stored) <= recent:
        fresh, old = stored, []
    else:
        fresh, old = stored[-recent:], stored[:-recent]

    summary = _build_summary(old)
    if summary:
        # Как системная вставка: факты из прошлого, без новых инструкций.
        messages.append({
            "role": "system",
            "content": "ИЗ БОЛЕЕ РАННЕГО РАЗГОВОРА (сжато, чтобы не повторять "
                       "дословно). Это факты из прошлого - используй их, когда "
                       "пользователь про них спрашивает:\n" + summary,
        })

    for m in fresh:
        if m.get("role") in ("user", "assistant") and m.get("content"):
            messages.append({"role": m["role"], "content": m["content"]})

    messages.append({"role": "user", "content": user_message})
    return messages


def stats(session_id: str) -> dict:
    """Сколько всего сообщений в сессии."""
    client = _get_client()
    if client is None:
        return {"messages": 0}
    try:
        resp = (client.table(TABLE)
                .select("id", count="exact")
                .eq("session_id", session_id)
                .execute())
        return {"messages": resp.count or 0}
    except Exception:
        return {"messages": 0}