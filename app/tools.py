"""Инструменты агента: файлы, веб-поиск, выполнение кода."""
import io
import re
import subprocess
import sys
from pathlib import Path

import httpx

from . import config
from . import infra as _infra

WORKSPACE = config.WORKSPACE


def _ws() -> Path:
    """Папка активного проекта.

    Читается при каждом вызове, а не хранится в константе: тогда
    переключение проекта в интерфейсе сразу меняет рабочую папку,
    без перезапуска сервера.
    """
    return config.current_workspace()


SYSTEM_PROMPT = """Ты — лёгкий автономный ИИ-агент. Работаешь в облаке на сервере.

Твои возможности:
- Создаёшь, читаешь и правишь файлы в рабочей папке проекта.
- Выгружаешь проект на GitHub: инструмент `github_sync` (автоматически создаёт репозиторий при необходимости и загружает все файлы проекта).
- Управляешь сервером и деплоем на Render: инструменты `infra_deploy_project`, `infra_services`, `infra_deploy`, `infra_logs`, `infra_env`.
- Сохраняешь и используешь API-ключи: `secret_set`, `secret_list`.
- Выполняешь вычисления и проверяешь код через execute_code и run_python_file.
- Ищешь информацию в интернете: web_search, fetch_url.
- Сохраняешь файлы в S3 хранилище: cloud_upload.

ГЛАВНЫЕ ПРАВИЛА:
1. КРИТИЧЕСКИ ВАЖНО: НИКОГДА не обещай на словах («Хорошо, сейчас сделаю», «Я загрузил проект на GitHub», «Я развернул сервис»), ЕСЛИ ТЫ НЕ ВЫЗВАЛ СООТВЕТСТВУЮЩИЙ ИНСТРУМЕНТ!
2. Если пользователь просит выгрузить, сохранить или обновить проект на GitHub — СРАЗУ ВЫЗЫВАЙ инструмент `github_sync` в первом же шаге!
3. Если пользователь просит развернуть / настроить сервер или задеплоить проект — СРАЗУ ВЫЗЫВАЙ `infra_deploy_project` или `infra_deploy`!
4. Для работы с GitHub НЕ пиши ручные скрипты git push через execute_code — используй готовый инструмент `github_sync`.
5. Если задача требует нескольких шагов — выполняй их по очереди через инструменты, а не описывай планы вместо выполнения.
6. Отвечай кратко, чётко, на языке пользователя, сообщая конкретные результаты и ссылки.
"""

_DANGEROUS = re.compile(
    r"(rm\s+-rf|:\(\)\s*\{|mkfs|dd\s+if=|shutdown|reboot|"
    r"curl\s+.*\|\s*(ba)?sh|wget\s+.*\|\s*(ba)?sh)",
    re.IGNORECASE,
)

SEARCH_URL = "https://html.duckduckgo.com/html/?q={}"


def _safe_path(rel: str, project: str = "") -> Path:
    """Путь внутри рабочей папки — защита от выхода за её пределы."""
    if not rel or not rel.strip():
        raise ValueError("Путь не указан")
    if ".." in rel or rel.startswith("/") or ":" in rel:
        raise ValueError("Недопустимый путь")
    base = config.project_dir(project) if project else _ws()
    full = (base / rel).resolve()
    if not str(full).startswith(str(base.resolve())):
        raise ValueError("Путь выходит за пределы папки проекта")
    return full


# Расширения, которые браузер умеет показать как картинку
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico"}
IMAGE_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".svg": "image/svg+xml", ".ico": "image/x-icon",
}


def is_image(path: str) -> bool:
    """Картинка ли это файла - по расширению."""
    return Path(path).suffix.lower() in IMAGE_EXT


def image_mime(path: str) -> str:
    """Content-Type для картинки."""
    return IMAGE_MIME.get(Path(path).suffix.lower(), "application/octet-stream")


# ---------------- Файлы ----------------

def tool_write_file(path: str, content: str) -> str:
    """Создаёт или перезаписывает текстовый файл в рабочей папке."""
    try:
        full = _safe_path(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
        size = len(content.encode("utf-8"))
        return f"Записан файл {path} ({size} байт)"
    except Exception as exc:
        return f"Ошибка записи: {exc}"


def tool_view_image(path: str) -> str:
    """Описывает изображение: размеры и содержимое как текст, если это SVG.

    Модель не умеет смотреть картинки напрямую, поэтому для растровых
    файлов возвращаем метаданные, а для SVG - текстовое содержимое,
    по которому можно понять, что нарисовано.
    """
    try:
        full = _safe_path(path)
        if not full.is_file():
            return f"Файл не найден: {path}"
        if not is_image(path):
            return f"{path} - не картинка (расширение {Path(path).suffix})"

        size = full.stat().st_size
        if full.suffix.lower() == ".svg":
            text = full.read_text(encoding="utf-8", errors="replace")
            labels = re.findall(r">([^<>]{2,60})<", text)
            shapes = len(re.findall(r"<(rect|circle|ellipse|path|line|polygon|polyline)",
                                    text))
            parts = [f"SVG-изображение {path} ({size} Б)",
                     f"фигур: {shapes}"]
            if labels:
                parts.append("текст на картинке: " + "; ".join(labels[:25]))
            return "\n".join(parts)

        dims = ""
        try:
            from PIL import Image  # опционально
            with Image.open(full) as im:
                dims = f", размер {im.width}x{im.height}, режим {im.mode}"
        except Exception:
            # Без PIL размеры читаем вручную для PNG
            if full.suffix.lower() == ".png":
                try:
                    head = full.read_bytes()[16:24]
                    dims = f", размер {int.from_bytes(head[:4], 'big')}" \
                           f"x{int.from_bytes(head[4:], 'big')}"
                except Exception:
                    pass
        return (f"Картинка {path} ({size} Б{dims}). "
                f"Растровое изображение - содержимое я описать не могу, "
                f"скажи пользователю открыть его в панели файлов.")
    except Exception as exc:
        return f"Ошибка просмотра: {exc}"


def tool_edit_file(path: str, old_text: str, new_text: str,
                   replace_all: bool = False) -> str:
    """Точечно меняет текст в файле: заменяет old_text на new_text."""
    try:
        full = _safe_path(path)
        if not full.is_file():
            return f"Файл не найден: {path}"
        content = full.read_text(encoding="utf-8", errors="replace")

        if old_text not in content:
            # Подсказываем похожее - обычно ошибка в пробелах или отступах
            hint = _closest_line(content, old_text)
            extra = (f"\nПохожего текста нет. Ближайшая строка: {hint}"
                     if hint else "")
            return (f"Текст для замены не найден в {path}.{extra}")

        count = content.count(old_text)
        content = content.replace(old_text, new_text) if replace_all \
            else content.replace(old_text, new_text, 1)
        full.write_text(content, encoding="utf-8")

        return (f"Изменён {path}: заменено "
                f"{'все вхождения' if replace_all else '1 место'} "
                f"(всего было {count})")
    except Exception as exc:
        return f"Ошибка правки: {exc}"


def _closest_line(content: str, needle: str, limit: int = 120) -> str:
    """Ищет строку, похожую на искомую - для подсказки при ошибке."""
    import difflib
    # Ищем по первой непустой строке запроса: пользователь может прислать
    # многострочный кусок, а совпадение искать надо по первой строке.
    lines_in = [ln.strip() for ln in (needle or "").splitlines() if ln.strip()]
    needle = lines_in[0] if lines_in else ""
    if not needle or len(needle) < 3:
        return ""
    matches = difflib.get_close_matches(needle, content.splitlines(), n=1,
                                         cutoff=0.5)
    return matches[0][:limit] if matches else ""


def tool_grep(pattern: str, glob: str = "*", ignore_case: bool = False,
              max_results: int = 40) -> str:
    """Ищет текст по файлам проекта. Возвращает совпадения с номерами строк."""
    try:
        try:
            rx = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        except re.error as exc:
            return f"Некорректное регулярное выражение: {exc}"

        hits: list[str] = []
        skipped = {"binary": 0, "big": 0}
        for path in _ws().rglob(glob):
            if not path.is_file() or len(hits) >= max_results:
                continue
            if any(part in (".git", "__pycache__", ".venv")
                   for part in path.parts):
                continue
            if path.suffix.lower() in IMAGE_EXT:
                skipped["binary"] += 1
                continue
            try:
                if path.stat().st_size > 2_000_000:
                    skipped["big"] += 1
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            for num, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    # Путь всегда со слэшами: на Windows str(Path) даёт
                    # обратные, а модель ищет по прямым.
                    rel = path.relative_to(_ws()).as_posix()
                    hits.append(f"{rel}:{num}: {line.strip()[:150]}")
                    if len(hits) >= max_results:
                        break

        if not hits:
            note = ""
            if skipped["binary"] or skipped["big"]:
                note = (f" Пропущено: {skipped['binary']} картинок, "
                        f"{skipped['big']} больших файлов.")
            return f"Ничего не найдено по запросу «{pattern}».{note}"

        note = ""
        if len(hits) >= max_results:
            note = f"\nПоказаны первые {max_results} совпадений."
        return "\n".join(hits) + note
    except Exception as exc:
        return f"Ошибка поиска: {exc}"


def tool_read_file(path: str) -> str:
    """Читает текстовый файл из рабочей папки."""
    try:
        full = _safe_path(path)
        if not full.exists():
            return f"Файл не найден: {path}"
        data = full.read_text(encoding="utf-8", errors="replace")
        if len(data) > 40000:
            return f"{path}\n\n[файл обрезан до 40000 символов]\n{data[:40000]}"
        return f"{path}\n\n{data}"
    except Exception as exc:
        return f"Ошибка чтения: {exc}"


def tool_list_files(directory: str = ".") -> str:
    """Показывает список файлов и папок в рабочей директории."""
    try:
        full = _safe_path(directory or ".")
        if not full.exists():
            return f"Директория не найдена: {directory}"
        entries = sorted(full.iterdir())
        if not entries:
            return f"{directory} — пусто"
        lines = []
        for e in entries[:200]:
            if e.is_dir():
                lines.append(f"[dir]  {e.name}/")
            else:
                lines.append(f"       {e.name} ({e.stat().st_size} Б)")
        return "\n".join(lines)
    except Exception as exc:
        return f"Ошибка: {exc}"


def tool_delete_file(path: str) -> str:
    """Удаляет файл из рабочей папки."""
    try:
        full = _safe_path(path)
        if full.is_dir():
            return "Это папка - используй delete_dir"
        if not full.exists():
            return f"Файл не найден: {path}"
        full.unlink()
        return f"Удалён файл {path}"
    except Exception as exc:
        return f"Ошибка удаления: {exc}"


# ---------------- Папки ----------------

def tool_make_dir(path: str) -> str:
    """Создаёт папку вместе с промежуточными."""
    try:
        full = _safe_path(path)
        if full.is_dir():
            return f"Папка уже существует: {path}"
        if full.exists():
            return f"По этому пути уже есть файл: {path}"
        full.mkdir(parents=True, exist_ok=True)
        return f"Создана папка: {path}"
    except Exception as exc:
        return f"Ошибка создания папки: {exc}"


def tool_delete_dir(path: str, recursive: bool = False) -> str:
    """Удаляет папку. Непустую - только с recursive."""
    try:
        full = _safe_path(path)
        if not full.is_dir():
            return f"Это не папка: {path}"
        if any(full.iterdir()) and not recursive:
            return (f"Папка {path} не пустая. Повтори с recursive=true, "
                    f"если нужно удалить её целиком.")
        import shutil
        if recursive:
            shutil.rmtree(full)
        else:
            full.rmdir()
        return f"Удалена папка: {path}"
    except Exception as exc:
        return f"Ошибка удаления папки: {exc}"


def tool_move_file(source: str, destination: str) -> str:
    """Перемещает или переименовывает файл."""
    try:
        src = _safe_path(source)
        dst = _safe_path(destination)
        if not src.exists():
            return f"Не найдено: {source}"
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.is_dir():
            dst = dst / src.name
        src.replace(dst)
        return f"Перемещено: {source} -> {dst.relative_to(_ws())}"
    except Exception as exc:
        return f"Ошибка перемещения: {exc}"


def tool_tree(path: str = ".", max_depth: int = 3) -> str:
    """Показывает дерево файлов и папок."""
    try:
        root = _safe_path(path or ".")
        if not root.exists():
            return f"Не найдено: {path}"

        out = [f"{path or '.'}/"]

        def walk(d, depth: int) -> None:
            if depth > max_depth:
                out.append("  " * depth + "...")
                return
            try:
                entries = sorted(d.iterdir(),
                                 key=lambda p: (not p.is_dir(), p.name.lower()))
            except Exception:
                return
            for e in entries:
                indent = "  " * depth + ("+-- " if depth else "|   ")
                if e.is_dir():
                    out.append(f"{indent}{e.name}/")
                    walk(e, depth + 1)
                else:
                    out.append(f"{indent}{e.name} ({e.stat().st_size} Б)")

        walk(root, 1)
        return "\n".join(out[:400])
    except Exception as exc:
        return f"Ошибка: {exc}"


# ---------------- Выполнение кода ----------------

def tool_execute_code(code: str) -> str:
    """Выполняет Python-код и возвращает результат. Для вычислений и проверки."""
    if _DANGEROUS.search(code):
        return "Отклонено: код содержит опасную конструкцию"

    import concurrent.futures
    import contextlib
    import os

    # Предотвращаем интерактивные окна git и авторизации
    os.environ["GIT_TERMINAL_PROMPT"] = "0"
    os.environ["GIT_ASKPASS"] = ""

    def _run():
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            exec(  # noqa: S102 - намеренный sandbox на уровне строк
                compile(code, "<agent>", "exec"),
                {"__name__": "__main__"},
            )
        return buf.getvalue()

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_run)
            try:
                out = future.result(timeout=25.0)
                return out[-8000:] if out else "Код выполнен, вывода нет"
            except concurrent.futures.TimeoutError:
                return "Ошибка: выполнение кода прервано по таймауту (25 сек). Код заблокировался или зациклился."
    except Exception:
        import traceback
        return traceback.format_exc()[-3000:]


def tool_run_python_file(path: str) -> str:
    """Запускает существующий Python-файл из рабочей папки."""
    try:
        full = _safe_path(path)
        if not full.exists():
            return f"Файл не найден: {path}"
        proc = subprocess.run(
            [sys.executable, str(full)], cwd=str(_ws()),
            capture_output=True, text=True, timeout=120,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        return f"[код {proc.returncode}]\n{output[-8000:]}"
    except subprocess.TimeoutExpired:
        return "Таймаут выполнения (120 сек)"
    except Exception as exc:
        return f"Ошибка запуска: {exc}"


# ---------------- Веб ----------------

def tool_web_search(query: str, max_results: int = 5) -> str:
    """Ищет информацию в интернете и возвращает сниппеты результатов."""
    try:
        with httpx.Client(timeout=20, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0 (agent)"}) as c:
            resp = c.get("https://html.duckduckgo.com/html/",
                         params={"q": query})
        html = resp.text
        titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', html, re.DOTALL)
        links = re.findall(r'class="result__a"[^>]*href="(.*?)"', html, re.DOTALL)
        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)

        def clean(s: str) -> str:
            return re.sub(r"<.*?>", "", s).strip()

        out = []
        for i, title in enumerate(titles[:max_results]):
            snippet = clean(snippets[i]) if i < len(snippets) else ""
            url = links[i] if i < len(links) else ""
            out.append(f"{i + 1}. {clean(title)}\n   {url}\n   {snippet[:300]}")
        return "\n\n".join(out) if out else "Ничего не найдено"
    except Exception as exc:
        return f"Ошибка поиска: {exc}"


def tool_fetch_url(url: str) -> str:
    """Открывает URL и возвращает текст страницы без HTML-разметки."""
    try:
        with httpx.Client(timeout=20, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0 (agent)"}) as c:
            resp = c.get(url)
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", resp.text,
                      flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<.*?>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:15000] if text else "Пустая страница"
    except Exception as exc:
        return f"Ошибка загрузки: {exc}"


def tool_cloud_upload(filename: str) -> str:
    """Выгружает файл из рабочей папки в облачное хранилище Storj S3."""
    from . import storage
    try:
        full = _safe_path(filename)
        if not full.exists():
            return f"Файл не найден: {filename}"
        key = f"workspace/{filename}"
        storage.upload_file(key, str(full))
        return f"Выгружено в облако: {key} ({full.stat().st_size} Б)"
    except Exception as exc:
        return f"Ошибка выгрузки: {exc}"



def _tool(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    """Собирает описание инструмента в формате OpenAI function calling."""
    params: dict = {"type": "object", "properties": properties}
    if required:
        params["required"] = required
    return {"type": "function", "function": {
        "name": name, "description": description, "parameters": params}}


_P = {"type": "string", "description": "Путь относительно рабочей папки"}


def build_tools() -> list[dict]:
    """Описания всех инструментов агента в формате function calling."""
    return [
        _tool("write_file", "Создаёт или перезаписывает текстовый файл.",
              {"path": _P, "content": {"type": "string", "description": "Содержимое файла"}},
              ["path", "content"]),
        _tool("edit_file", "Точечно меняет текст в существующем файле. "
                           "Используй вместо перезаписи всего файла, когда "
                           "надо поменять одну-две строки.",
              {"path": _P,
               "old_text": {"type": "string", "description": "Что заменить (дословно)"},
               "new_text": {"type": "string", "description": "На что заменить"},
               "replace_all": {"type": "boolean", "description": "Заменить все вхождения"}},
              ["path", "old_text", "new_text"]),
        _tool("view_image", "Показывает информацию об изображении: размеры, "
                            "а для SVG - ещё и текст на картинке.",
              {"path": _P}, ["path"]),
        _tool("grep", "Ищет текст по всем файлам проекта. Быстрее, чем открывать "
                      "файлы по одному.",
              {"pattern": {"type": "string", "description": "Регулярное выражение"},
               "glob": {"type": "string", "description": "Маска файлов, по умолчанию '*'"},
               "ignore_case": {"type": "boolean", "description": "Игнорировать регистр"},
               "max_results": {"type": "integer", "description": "Сколько совпадений"}},
              ["pattern"]),
        _tool("read_file", "Читает текстовый файл. Для просмотра содержимого.",
              {"path": _P}, ["path"]),
        _tool("list_files", "Показывает содержимое директории. По умолчанию '.'.",
              {"directory": {"type": "string", "description": "Директория, по умолчанию '.'"}}),
        _tool("tree", "Показывает дерево файлов и папок - удобно, когда их много.",
              {"path": _P,
               "max_depth": {"type": "integer", "description": "Максимальная глубина, по умолчанию 3"}},
              ["path"]),
        _tool("make_dir", "Создаёт папку, включая промежуточные. Сделай это перед созданием файлов проекта.",
              {"path": _P}, ["path"]),
        _tool("delete_dir", "Удаляет папку. Если непустая - требуется recursive=true.",
              {"path": _P,
               "recursive": {"type": "boolean", "description": "Удалять вместе с содержимым"}},
              ["path"]),
        _tool("move_file", "Перемещает или переименовывает файл.",
              {"source": {"type": "string", "description": "Откуда"},
               "destination": {"type": "string", "description": "Куда"}},
              ["source", "destination"]),
        _tool("delete_file", "Удаляет файл (не папку).",
              {"path": _P}, ["path"]),
        _tool("execute_code", "Выполняет Python-код и возвращает вывод. Для вычислений и проверки гипотез.",
              {"code": {"type": "string"}}, ["code"]),
        _tool("run_python_file", "Запускает Python-файл из рабочей папки и возвращает вывод.",
              {"path": _P}, ["path"]),
        _tool("web_search", "Ищет информацию в интернете.",
              {"query": {"type": "string"},
               "max_results": {"type": "integer", "description": "Сколько результатов (по умолч. 5)"}},
              ["query"]),
        _tool("fetch_url", "Загружает страницу по адресу и возвращает её текст.",
              {"url": {"type": "string"}}, ["url"]),
        _tool("cloud_upload", "Выгружает файл из рабочей папки в облачное хранилище Storj.",
              {"filename": {"type": "string", "description": "Имя файла в рабочей папке"}},
              ["filename"]),

        # ---------- Секреты ----------
        _tool("secret_set", "Сохраняет API-ключ в зашифрованное хранилище. "
                            "Ключ НЕ попадает в файлы проекта и не утечёт в git. "
                            "Имя должно начинаться с RENDER_, GITHUB_, VERCEL_, "
                            "AWS_, SUPABASE_, OPENROUTER_, GROQ_, GEMINI_ или AG_.",
              {"name": {"type": "string", "description": "Имя секрета, например RENDER_API_KEY"},
               "value": {"type": "string", "description": "Само значение ключа"},
               "note": {"type": "string", "description": "Заметка для себя, что это за ключ"}},
              ["name", "value"]),
        _tool("secret_list", "Показывает список сохранённых секретов. Значения не показывает.",
              {}),
        _tool("secret_delete", "Удаляет секрет из хранилища.",
              {"name": {"type": "string"}}, ["name"]),

        # ---------- Инфраструктура ----------
        _tool("infra_services", "Показывает все сервисы на Render: id, адреса, репозитории.",
              {}),
        _tool("infra_env", "Управляет переменными окружения сервиса на Render. "
                           "action=list показать, set добавить, delete удалить. "
                           "secret=true - сохранить значение как секрет.",
              {"service_id": {"type": "string", "description": "id сервиса из infra_services"},
               "action": {"type": "string", "description": "list, set или delete"},
               "name": {"type": "string", "description": "Имя переменной"},
               "value": {"type": "string", "description": "Значение переменной"},
               "secret": {"type": "boolean", "description": "Скрыть значение в интерфейсе"}},
              ["service_id"]),
        _tool("infra_deploy", "Запускает новый деплой сервиса на Render.",
              {"service_id": {"type": "string"},
               "clear_cache": {"type": "boolean", "description": "Сбросить кэш сборки"}},
              ["service_id"]),
        _tool("infra_deploy_status", "Показывает статус деплоя по его id.",
              {"deploy_id": {"type": "string"}}, ["deploy_id"]),
        _tool("infra_logs", "Показывает последние строки логов деплоя.",
              {"deploy_id": {"type": "string"},
               "tail": {"type": "integer", "description": "Сколько строк, по умолч. 60"}},
              ["deploy_id"]),
        _tool("infra_create", "Создаёт сервис на Render из GitHub-репозитория. "
                              "Если API вернёт ошибку - создай через render.yaml.",
              {"name": {"type": "string"},
               "repo": {"type": "string", "description": "https://github.com/user/repo"},
               "branch": {"type": "string"},
               "plan": {"type": "string", "description": "free или starter"},
               "build_command": {"type": "string"},
               "start_command": {"type": "string"},
               "owner_id": {"type": "string", "description": "id рабочего пространства Render"}},
              ["name", "repo"]),

        # ---------- GitHub и Деплой ----------
        _tool("github_sync", "Выгружает и синхронизирует проект на GitHub в репозиторий. "
                             "Автоматически создаёт репозиторий, если его ещё нет, "
                             "и передаёт все файлы проекта через GitHub API. "
                             "Используй этот инструмент, когда пользователь просит "
                             "выгрузить, залить или сохранить проект на GitHub.",
              {"repo_name": {"type": "string", "description": "Имя репозитория (по умолчанию имя текущего проекта)"},
               "branch": {"type": "string", "description": "Ветка (по умолчанию main)"},
               "message": {"type": "string", "description": "Сообщение коммита"},
               "private": {"type": "boolean", "description": "Сделать репозиторий приватным"},
               "all_repo": {"type": "boolean", "description": "Выгрузить корневой репозиторий light-agent (по умолч. false)"}}),
        _tool("infra_deploy_project", "Развёртывает проект на сервере Render. "
                                      "Если сервис уже существует (например light-agent) — запускает свежий деплой. "
                                      "Если нет — создаёт веб-сервис на Render из указанного репозитория GitHub. "
                                      "Используй, когда пользователь просит настроить сервер или задеплоить проект.",
              {"service_name": {"type": "string", "description": "Имя сервиса на Render (по умолчанию light-agent или имя проекта)"},
               "repo_url": {"type": "string", "description": "URL репозитория GitHub (необязательно)"},
               "branch": {"type": "string", "description": "Ветка (по умолчанию main)"},
               "clear_cache": {"type": "boolean", "description": "Сбросить кэш сборки (по умолчанию true)"}}),

        # ---------- HTTP ----------
        _tool("http_request", "Делает HTTP-запрос к API облачного провайдера "
                              "(Render, Vercel, GitHub, AWS). "
                              "В заголовках пиши @NAME - значение секрета "
                              "подставится само, ключ не попадёт в файлы.",
              {"method": {"type": "string", "description": "GET, POST, PUT, PATCH, DELETE"},
               "url": {"type": "string", "description": "Полный https-адрес"},
               "headers": {"type": "string", "description": "JSON-объект заголовков"},
               "body": {"type": "string", "description": "Тело запроса, JSON"},
               "auth_secret": {"type": "string", "description": "Имя секрета для Authorization, например @GITHUB_TOKEN"},
               "timeout": {"type": "integer", "description": "Таймаут, сек (макс 180)"}},
              ["method", "url"]),
    ]


_REGISTRY: dict[str, object] = {
    "write_file": tool_write_file,
    "edit_file": tool_edit_file,
    "view_image": tool_view_image,
    "grep": tool_grep,
    "read_file": tool_read_file,
    # Инфраструктура и секреты живут в отдельном модуле: там бóльше
    # кода и своя логика (шифрование, HTTP, работа с Render API).
    "secret_set": _infra.tool_secret_set,
    "secret_list": _infra.tool_secret_list,
    "secret_delete": _infra.tool_secret_delete,
    "infra_services": _infra.tool_infra_services,
    "infra_env": _infra.tool_infra_env,
    "infra_deploy": _infra.tool_infra_deploy,
    "infra_deploy_status": _infra.tool_infra_deploy_status,
    "infra_logs": _infra.tool_infra_logs,
    "infra_create": _infra.tool_infra_create,
    "github_sync": _infra.tool_github_sync,
    "infra_deploy_project": _infra.tool_infra_deploy_project,
    "http_request": _infra.tool_http_request,
    "list_files": tool_list_files,
    "tree": tool_tree,
    "make_dir": tool_make_dir,
    "delete_dir": tool_delete_dir,
    "move_file": tool_move_file,
    "delete_file": tool_delete_file,
    "execute_code": tool_execute_code,
    "run_python_file": tool_run_python_file,
    "web_search": tool_web_search,
    "search_web": tool_web_search,
    "search": tool_web_search,
    "fetch_url": tool_fetch_url,
    "cloud_upload": tool_cloud_upload,
}


def execute(name: str, args: dict) -> str:
    """Вызывает инструмент по имени."""
    fn = _REGISTRY.get(name)
    if fn is None:
        return f"Неизвестный инструмент: {name}"
    try:
        return str(fn(**args))
    except TypeError as exc:
        return f"Неверные аргументы для {name}: {exc}"
    except Exception as exc:
        return f"Ошибка инструмента {name}: {exc}"