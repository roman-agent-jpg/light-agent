"""FastAPI: REST API, SSE-стриминг, файловый менеджер.

Производительность на бесплатном Render (512 МБ RAM, 1 CPU):
- блокирующие вызовы LLM уходят в поток через ThreadPoolExecutor,
  поэтому сервер не зависает, даже если агент думает 40 секунд;
- SSE отдаёт события по мере поступления, а не в конце;
- у каждого запроса есть таймаут и возможность отмены.
"""
import asyncio
import json
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import (agent, config, infra, keepsleep, llm, memory, projects,
               storage, tools, vault)

WEB_DIR = config.BASE_DIR / "web"

# Мусор, который уборщик вправе удалять. Всё остальное не трогаем.
CLEANABLE_PREFIXES = ("tmp_", "diag_", "test_")
CLEANABLE_SUFFIXES = (".tmp", ".log", ".bak", ".old")

# Сколько живёт SSE-соединение, чтобы не держать поток вечно
SSE_MAX_SECONDS = 300


@asynccontextmanager
async def lifespan(app: FastAPI):
    if memory.ensure_schema():
        print("[start] Supabase schema ready", flush=True)
    # Инициализация схемы проектов, фактов и журнала действий
    pres = projects.ensure_schema()
    print(f"[start] projects schema: {pres}", flush=True)
    # Хранилище секретов: таблица создаётся сама через Management API
    vres = vault.ensure_table()
    print(f"[start] vault: {vres}", flush=True)
    if storage.ensure_bucket():
        print(f"[start] bucket {config.S3_BUCKET} ready", flush=True)
    keepsleep.start()
    yield
    await keepsleep.stop()


app = FastAPI(title="Light Agent", version="2.0", lifespan=lifespan)


# ---------------- Схемы ----------------

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    session_id: str = Field("default", max_length=100)
    project: str = Field("default", max_length=100)
    model: str | None = None


class SessionRequest(BaseModel):
    session_id: str = Field("default", max_length=100)
    project: str = Field("default", max_length=100)


class ProjectCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field("", max_length=500)


class FileBody(BaseModel):
    path: str = Field(..., max_length=500)
    content: str = ""
    project: str = Field("default", max_length=100)


class PathBody(BaseModel):
    path: str = Field(..., max_length=500)
    project: str = Field("default", max_length=100)


# ---------------- Служебное ----------------

def _safe(rel: str, project: str = ""):
    """Путь внутри рабочей папки проекта.

    Попытка выйти за пределы даёт 400, а не 500.
    """
    try:
        return tools._safe_path(rel or ".", project=project)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _sse(event: str, data: dict) -> str:
    """Форматирует одно SSE-событие."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ---------------- Базовые ----------------

@app.get("/health")
async def health():
    """Лёгкий эндпоинт для keepalive. Должен отвечать быстро."""
    return {
        "status": "ok",
        "idle_seconds": round(agent.idle_seconds()),
        "running_jobs": agent.state()["running_jobs"],
    }


@app.get("/", response_class=HTMLResponse)
async def index():
    """Главная страница. Заголовки нужны для установки как приложение (PWA)."""
    resp = FileResponse(WEB_DIR / "index.html")
    # Service worker нельзя отдавать из кэша HTTP — иначе браузер
    # не увидит обновление и приложение застрянет на старой версии.
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@app.get("/manifest.json")
async def manifest():
    """Манифест PWA. Отдаём из /static, но с правильным Content-Type."""
    path = WEB_DIR / "static" / "manifest.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Манифест не найден")
    return FileResponse(path, media_type="application/manifest+json")


@app.get("/api/status")
async def status():
    return {
        "providers": [
            {
                "name": p["name"],
                "models": p["models"],
                "model_labels": p.get("model_labels", {}),
                "base_url": p["base_url"],
            }
            for p in config.providers()
        ],
        "llm_stats": llm.stats(),
        "memory": memory.status(),
        "storage": storage.status(),
        "anti_sleep": keepsleep.status(),
        "instructions": keepsleep.instructions(),
        "workspace": str(config.WORKSPACE),
        "agent": agent.state(),
        "tools": [t["function"]["name"] for t in tools.build_tools()],
        "infra": infra.status(),
    }


# ---------------- Чат ----------------

@app.post("/api/chat")
async def chat(req: ChatRequest, model: str | None = None):
    """Обычный запрос: ждём полный ответ."""
    try:
        chosen_model = req.model or model
        job = agent.submit(req.session_id, req.message, project=req.project, model=chosen_model)
        return await asyncio.to_thread(job.result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest, model: str | None = None):
    """SSE-стриминг: события приходят по мере работы агента.

    События:
      step    — номер итерации
      tool    — модель вызвала инструмент
      done    — финальный ответ
      error   — что-то пошло не так
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    job_holder: dict = {}

    def on_event(kind: str, payload: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, (kind, payload))

    async def gen():
        started = time.monotonic()
        try:
            chosen_model = req.model or model
            job_holder["job"] = agent.submit(req.session_id, req.message,
                                            on_event=on_event, project=req.project,
                                            model=chosen_model)
            yield _sse("open", {"session_id": req.session_id, "project": req.project})

            while True:
                if time.monotonic() - started > SSE_MAX_SECONDS:
                    job_holder["job"].cancel()
                    yield _sse("error", {"error": "Превышен лимит времени"})
                    return

                # Отдаём накопленные события, не блокируя поток агента
                while not queue.empty():
                    kind, payload = queue.get_nowait()
                    yield _sse(kind, payload)

                job = job_holder.get("job")
                if job and job.done():
                    await asyncio.sleep(0.05)
                    while not queue.empty():
                        kind, payload = queue.get_nowait()
                        yield _sse(kind, payload)
                    result = job.result()
                    yield _sse("done", result)
                    return

                await asyncio.sleep(0.08)

        except asyncio.CancelledError:
            job = job_holder.get("job")
            if job:
                job.cancel()
            raise
        except Exception as exc:
            yield _sse("error", {"error": f"{type(exc).__name__}: {exc}"})

    return StreamingResponse(gen(),
                             media_type="text/event-stream",
                             headers={
                                 "Cache-Control": "no-cache",
                                 "X-Accel-Buffering": "no",
                                 "Connection": "keep-alive",
                             })


@app.post("/api/cancel")
async def cancel(req: SessionRequest):
    """Останавливает текущую задачу сессии."""
    ok = agent.cancel(req.session_id)
    return {"ok": ok, "session_id": req.session_id,
            "message": "Задача отменена" if ok else "Нечего отменять"}


# ---------------- Проекты ----------------

@app.get("/api/projects")
async def api_get_projects():
    """Список всех проектов (из Supabase + папки в workspace)."""
    return {"projects": projects.list_projects(), "active": config.get_current_project()}


@app.post("/api/projects")
async def api_create_project(req: ProjectCreateRequest):
    """Создаёт новый проект и физическую папку для него."""
    res = projects.create_project(req.name, req.description)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Ошибка создания проекта"))
    return res


@app.delete("/api/projects/{name}")
async def api_delete_project(name: str):
    """Удаляет проект."""
    if name.lower() == "default":
        raise HTTPException(status_code=400, detail="Нельзя удалить основной проект")
    res = projects.delete_project(name)
    return res


# ---------------- Память и диалоги ----------------

@app.post("/api/reset")
async def reset(req: SessionRequest):
    ok = memory.clear_session(req.session_id)
    return {"ok": ok, "session_id": req.session_id}


@app.get("/api/sessions")
async def sessions(project: str = Query(None)):
    """Список сессий. Если передан project, фильтрует по нему."""
    proj = project if isinstance(project, str) else None
    return {"sessions": memory.list_sessions(limit=50, project=proj)}


@app.get("/api/project/{project}/messages")
async def project_messages(project: str, limit: int = Query(100, ge=1, le=500)):
    """Возвращает всю историю переписки конкретной папки/проекта."""
    lim = limit if isinstance(limit, int) else 100
    msgs = memory.get_project_history(project, limit=lim)
    return {"ok": True, "project": project, "messages": msgs}


@app.get("/api/session/{session_id}/messages")
async def session_messages(session_id: str, limit: int = Query(100, ge=1, le=500)):
    """Возвращает историю сообщений диалога для загрузки на любом устройстве."""
    lim = limit if isinstance(limit, int) else 100
    msgs = memory.get_history(session_id, limit=lim)
    return {"ok": True, "session_id": session_id, "messages": msgs}


@app.get("/api/history")
async def api_history(session_id: str = Query(...), limit: int = Query(100, ge=1, le=500)):
    """Алиас для загрузки истории диалога."""
    sid = session_id if isinstance(session_id, str) else str(session_id)
    lim = limit if isinstance(limit, int) else 100
    msgs = memory.get_history(sid, limit=lim)
    return {"ok": True, "session_id": sid, "messages": msgs}


@app.delete("/api/session/{session_id}")
async def api_delete_session(session_id: str):
    """Удаляет сессию диалога."""
    ok = memory.delete_session(session_id)
    return {"ok": ok, "session_id": session_id}


# ---------------- Файлы проекта ----------------

@app.get("/api/tree")
async def api_tree(path: str = ".", depth: int = Query(4, ge=1, le=10),
                   project: str = Query("default")):
    """Структура конкретного проекта."""
    proj = project if isinstance(project, str) else "default"
    base = config.project_dir(proj)
    limit_depth = depth if isinstance(depth, int) else 4

    def build(p, level: int) -> list[dict]:
        if level > limit_depth:
            return []
        items: list[dict] = []
        try:
            entries = sorted(p.iterdir(),
                             key=lambda x: (not x.is_dir(), x.name.lower()))
        except Exception:
            return []
        for e in entries:
            if e.name.startswith("."):
                continue
            rel = str(e.relative_to(base)).replace("\\", "/")
            if e.is_dir():
                items.append({"type": "dir", "name": e.name, "path": rel,
                              "children": build(e, level + 1)})
            else:
                try:
                    size = e.stat().st_size
                except OSError:
                    size = 0
                items.append({
                    "type": "image" if tools.is_image(e.name) else "file",
                    "name": e.name, "path": rel, "size": size,
                })
        return items

    target = _safe(path, project=proj)
    return {"path": path, "tree": build(target, 1),
            "project": proj, "workspace": str(base)}


@app.get("/api/raw")
async def api_raw(path: str, project: str = Query("default")):
    """Отдаёт файл из папки проекта как есть."""
    proj = project if isinstance(project, str) else "default"
    full = _safe(path, project=proj)
    if not full.is_file():
        raise HTTPException(status_code=404, detail="Файл не найден")
    return FileResponse(full)


@app.get("/api/file")
async def api_read(path: str, project: str = Query("default")):
    """Читает текстовый файл из проекта для просмотра и правки."""
    proj = project if isinstance(project, str) else "default"
    full = _safe(path, project=proj)
    if not full.is_file():
        raise HTTPException(status_code=404, detail="Файл не найден")
    if tools.is_image(str(full)):
        return {"path": path, "is_image": True,
                "mime": tools.image_mime(str(full)),
                "size": full.stat().st_size}
    try:
        if full.stat().st_size > 400_000:
            return {"path": path, "too_big": True,
                    "message": "Файл больше 400 КБ - скачай его вместо просмотра"}
        return {"path": path, "is_image": False,
                "content": full.read_text(encoding="utf-8", errors="replace")}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.put("/api/file")
async def api_save_file(body: FileBody):
    """Сохраняет содержимое файла внутри активного проекта."""
    if not body.path.strip():
        raise HTTPException(status_code=400, detail="Не указан путь")
    if tools.is_image(body.path):
        raise HTTPException(status_code=400,
                            detail="Картинку нельзя править текстом")
    full = _safe(body.path, project=body.project)
    try:
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(body.content, encoding="utf-8")
        return {"ok": True, "path": body.path, "size": full.stat().st_size}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/mkdir")
async def api_mkdir(body: PathBody):
    """Создаёт папку внутри проекта."""
    if not body.path.strip():
        raise HTTPException(status_code=400, detail="Не указан путь")
    full = _safe(body.path, project=body.project)
    try:
        full.mkdir(parents=True, exist_ok=True)
        return {"ok": True, "path": body.path}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/file")
async def api_delete(path: str, recursive: bool = False, project: str = Query("default")):
    """Удаляет файл или папку внутри проекта."""
    proj = project if isinstance(project, str) else "default"
    full = _safe(path, project=proj)
    try:
        if full.is_dir():
            if any(full.iterdir()) and not recursive:
                raise HTTPException(
                    status_code=400,
                    detail="Папка не пустая. Повтори с recursive=true")
            import shutil
            shutil.rmtree(full) if recursive else full.rmdir()
        elif full.is_file():
            full.unlink()
        else:
            raise HTTPException(status_code=404, detail="Не найдено")
        return {"ok": True, "path": path}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...), project: str = Query("default")):
    """Загружает файл с компьютера в рабочую папку проекта."""
    proj = project if isinstance(project, str) else "default"
    name = os.path.basename((file.filename or "").replace("\\", "/"))
    if not name or name in (".", ".."):
        raise HTTPException(status_code=400, detail="Недопустимое имя файла")

    data = await file.read()
    if len(data) > 5_000_000:
        raise HTTPException(status_code=413,
                            detail="Файл больше 5 МБ - сначала сожми его")

    full = _safe(".", project=proj) / name
    full.write_bytes(data)
    return {"ok": True, "name": name, "size": len(data), "path": name,
            "is_image": tools.is_image(name)}


@app.post("/api/rename")
async def api_rename(body: dict):
    """Переименовывает или перемещает файл в проекте."""
    src = (body or {}).get("source", "")
    dst = (body or {}).get("destination", "")
    proj = (body or {}).get("project", "default")
    if not src or not dst:
        raise HTTPException(status_code=400, detail="Нужны source и destination")
    config.set_current_project(proj)
    result = tools.tool_move_file(src, dst)
    if result.startswith("Ошибка"):
        raise HTTPException(status_code=400, detail=result)
    return {"ok": True, "message": result}


# ---------------- Облачное хранилище ----------------

@app.get("/api/files")
async def files(prefix: str = ""):
    return {"files": storage.list_keys(prefix)}


# ---------------- Секреты (шифрованное хранилище) ----------------

@app.post("/admin/vault")
async def vault_set(body: dict,
                    authorization: str | None = Header(default=None)):
    """Сохраняет секрет в зашифрованное хранилище.

    Требует токен админа: через веб-интерфейс ключ не должен попасть
    в историю браузера или в логи.
    """
    _require_admin(authorization)
    name = (body or {}).get("name", "")
    value = (body or {}).get("value", "")
    note = (body or {}).get("note", "")
    res = vault.set_secret(name, value, note)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return res


@app.get("/api/vault")
async def vault_list():
    """Список секретов. Значения НЕ отдаются - только имена и заметки."""
    items = vault.list_secrets()
    return {"secrets": items, "status": vault.status()}


@app.delete("/admin/vault")
async def vault_delete(name: str,
                       authorization: str | None = Header(default=None)):
    """Удаляет секрет."""
    _require_admin(authorization)
    res = vault.delete_secret(name)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error"))
    return res


# ---------------- Уборка (GitHub Actions) ----------------

def _require_admin(authorization: str | None) -> None:
    expected = config.ADMIN_TOKEN
    if not expected or expected == "dev-token":
        raise HTTPException(status_code=503,
                            detail="ADMIN_TOKEN не настроен - уборка отключена")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Нужен заголовок Authorization")
    if authorization.removeprefix("Bearer ").strip() != expected:
        raise HTTPException(status_code=403, detail="Неверный токен")


class CleanupRequest(BaseModel):
    dry_run: bool = True
    max_age_hours: int = 24


def _plan_cleanup(max_age_hours: int) -> list[dict]:
    """Находит мусор для удаления. Ничего не удаляет."""
    ws = config.WORKSPACE
    now = time.time()
    victims: list[dict] = []

    for path in ws.rglob("*"):
        if not path.is_file():
            continue
        name = path.name
        if not (name.startswith(CLEANABLE_PREFIXES)
                or name.endswith(CLEANABLE_SUFFIXES)):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        age_h = (now - stat.st_mtime) / 3600
        if age_h < max_age_hours:
            continue
        victims.append({
            "name": str(path.relative_to(ws)),
            "size": stat.st_size,
            "age_hours": round(age_h, 1),
        })
    return victims


@app.post("/admin/cleanup")
async def admin_cleanup(req: CleanupRequest,
                         authorization: str | None = Header(default=None)):
    """Уборка рабочей папки. Запускается из GitHub Actions по расписанию."""
    _require_admin(authorization)

    victims = _plan_cleanup(max(1, req.max_age_hours))
    freed = sum(v["size"] for v in victims)
    removed: list[str] = []

    if not req.dry_run:
        for v in victims:
            try:
                (config.WORKSPACE / v["name"]).unlink()
                removed.append(v["name"])
            except OSError:
                pass

    return {"ok": True, "dry_run": req.dry_run, "found": len(victims),
            "removed": len(removed), "freed_bytes": freed,
            "removed_files": removed,
            "files": victims if req.dry_run else []}


@app.get("/admin/cleanup/preview")
async def admin_cleanup_preview(max_age_hours: int = 24,
                                authorization: str | None = Header(default=None)):
    """Показывает, что уборка удалит, ничего не удаляя."""
    _require_admin(authorization)
    victims = _plan_cleanup(max(1, max_age_hours))
    return {"found": len(victims),
            "freed_bytes": sum(v["size"] for v in victims),
            "files": victims}


# ---------------- Ошибки ----------------

@app.exception_handler(404)
async def not_found(request, exc):
    """404 в JSON, а не HTML - интерфейсу так удобнее."""
    if request.url.path.startswith("/api"):
        return JSONResponse({"detail": "Не найдено"}, status_code=404)
    return JSONResponse({"detail": "Страница не найдена"}, status_code=404)


# Статика лежит в web/static, а монтируется под /static
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")