"""Конфигурация из переменных окружения."""
import contextvars
import os
from pathlib import Path

from dotenv import load_dotenv

try:
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent
# Корневая папка для всех проектов. Каждый проект - подпапка внутри,
# поэтому файлы разных проектов физически изолированы друг от друга.
WORKSPACE = BASE_DIR / "workspace"

# Контекстная переменная для изолированной работы потоков/сессий над разными проектами
_project_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("current_project", default="default")
CURRENT_PROJECT = "default"


def set_current_project(name: str) -> str:
    """Задаёт активный проект для текущего контекста/потока."""
    global CURRENT_PROJECT
    val = (name or "default").strip() or "default"
    CURRENT_PROJECT = val
    _project_ctx.set(val)
    return val


def get_current_project() -> str:
    """Возвращает имя активного проекта текущего контекста."""
    try:
        val = _project_ctx.get()
        if val:
            return val
    except Exception:
        pass
    return CURRENT_PROJECT or "default"


load_dotenv(BASE_DIR / ".env")


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _ref_from_url() -> str:
    """Достаёт ref проекта из SUPABASE_URL вида https://xxxx.supabase.co."""
    url = os.getenv("SUPABASE_URL", "").strip()
    if not url:
        return ""
    host = url.replace("https://", "").replace("http://", "")
    host = host.split("/")[0]
    if host.endswith(".supabase.co"):
        return host[: -len(".supabase.co")]
    return ""


# ---------- LLM ----------
AG_BASE_URL = _env("AG_BASE_URL", "https://agent-master-server.onrender.com/v1")
AG_API_KEY = _env("AG_API_KEY")
AG_MODEL = _env("AG_MODEL", "antigravity-3.8-flash")
AG_MODEL_FALLBACK = _env("AG_MODEL_FALLBACK", "antigravity-3.8-pro")

OPENROUTER_API_KEY = _env("OPENROUTER_API_KEY")
OPENROUTER_MODEL = _env("OPENROUTER_MODEL", "deepseek/deepseek-chat")
GROQ_API_KEY = _env("GROQ_API_KEY")
GROQ_MODEL = _env("GROQ_MODEL", "llama-3.3-70b-versatile")
GEMINI_API_KEY = _env("GEMINI_API_KEY")

# ---------- Прочее ----------
SERVICE_URL = _env("SERVICE_URL")
IDLE_SLEEP_MINUTES = int(_env("IDLE_SLEEP_MINUTES", "14") or 14)
ADMIN_TOKEN = _env("ADMIN_TOKEN", "dev-token")

SUPABASE_URL = _env("SUPABASE_URL")
SUPABASE_KEY = _env("SUPABASE_KEY")
# Management API: нужен, чтобы агент мог сам создавать таблицы
# (обычный клиент supabase не умеет DDL).
SUPABASE_ACCESS_TOKEN = _env("SUPABASE_ACCESS_TOKEN")
SUPABASE_PROJECT_REF = _env("SUPABASE_PROJECT_REF") or _ref_from_url()

# Render: агент управляет деплоем через API
RENDER_API_KEY = _env("RENDER_API_KEY")
RENDER_OWNER_ID = _env("RENDER_OWNER_ID")

S3_ENDPOINT = _env("S3_ENDPOINT", "https://gateway.storjshare.io")
S3_ACCESS_KEY = _env("S3_ACCESS_KEY")
S3_SECRET_KEY = _env("S3_SECRET_KEY")
S3_BUCKET = _env("S3_BUCKET", "agent-files")
S3_REGION = _env("S3_REGION", "us-east-1")

WORKSPACE.mkdir(parents=True, exist_ok=True)


def project_dir(project: str = "") -> Path:
    """Папка конкретного проекта внутри рабочей области.

    Файлы разных проектов не пересекаются: project_dir("a") и
    project_dir("b") — это разные подпапки. Так переключение проекта
    в интерфейсе меняет набор файлов без перезапуска сервера.
    """
    name = (project or get_current_project() or "default").strip() or "default"
    # Только безопасные символы: имя проекта не должно уйти из папки
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in name)
    safe = safe.strip("-") or "default"
    d = WORKSPACE / safe
    d.mkdir(parents=True, exist_ok=True)
    return d


def use_project(name: str) -> Path:
    """Переключает активный проект и возвращает его папку."""
    set_current_project(name)
    return project_dir(name)


def current_workspace() -> Path:
    """Папка активного проекта - её отдают инструментам."""
    return project_dir(get_current_project())


def memory_enabled() -> bool:
    return bool(SUPABASE_URL and SUPABASE_KEY)


def storage_enabled() -> bool:
    return bool(S3_ACCESS_KEY and S3_SECRET_KEY)


def providers() -> list[dict]:
    """Каскад провайдеров: первый доступный — основной, остальные — фолбэк."""
    chain: list[dict] = [
        {
            "name": "Antigravity Pro",
            "base_url": AG_BASE_URL,
            "api_key": AG_API_KEY,
            "models": [AG_MODEL, AG_MODEL_FALLBACK],
            "model_labels": {
                AG_MODEL: "Antigravity 3.8 Flash (Pro) (antigravity-3.8-flash)",
                AG_MODEL_FALLBACK: "Antigravity 3.8 Pro (Pro) (antigravity-3.8-pro)",
            },
        }
    ]
    if OPENROUTER_API_KEY:
        chain.append(
            {
                "name": "openrouter",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key": OPENROUTER_API_KEY,
                "models": [OPENROUTER_MODEL],
            }
        )
    if GROQ_API_KEY:
        chain.append(
            {
                "name": "groq",
                "base_url": "https://api.groq.com/openai/v1",
                "api_key": GROQ_API_KEY,
                "models": [GROQ_MODEL],
            }
        )
    if GEMINI_API_KEY:
        chain.append(
            {
                "name": "gemini",
                "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                "api_key": GEMINI_API_KEY,
                "models": ["gemini-2.0-flash"],
            }
        )
    return [p for p in chain if p["api_key"]]