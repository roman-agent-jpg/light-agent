"""Диагностика моста Antigravity.

Важно: результат пишется в UTF-8 файл и в консоль выводится ТОЛЬКО
ASCII-представление, чтобы искажение кодировки в консоли
не могло скрыть реальную картину.

Запуск:  python diag_bridge.py
"""
import json
import os

from openai import OpenAI

from app import config

OUT_DIR = "reports"
os.makedirs(OUT_DIR, exist_ok=True)
OUT = os.path.join(OUT_DIR, "bridge_diag.json")

client = OpenAI(
    api_key=config.AG_API_KEY,
    base_url=config.AG_BASE_URL,
    timeout=180.0,
    max_retries=0,
)

report: dict = {"base_url": config.AG_BASE_URL, "checks": {}}


def safe(label: str, value) -> str:
    """ASCII-безопасный вывод: нельзя исказить кодировкой терминала."""
    return f"{label}: {value!r}"


# ---------- 1. Список моделей ----------
try:
    models = client.models.list()
    ids = sorted(m.id for m in models.data)
    report["checks"]["models"] = {"ok": True, "count": len(ids), "ids": ids}
    print(safe("models", ids))
except Exception as e:
    report["checks"]["models"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    print(safe("models ERROR", report["checks"]["models"]["error"]))


# ---------- 2. Русский текст БЕЗ tools ----------
RU_PROMPT = "Ответь одним предложением: какая сегодня погода в Киеве? Ответь по-русски."
for model in ["antigravity-3.8-pro", "antigravity-3.8-flash",
              "claude-3-5-sonnet-20241022", "gpt-4o"]:
    entry: dict = {}
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": RU_PROMPT}],
            max_tokens=200,
        )
        raw = r.choices[0].message.content or ""
        entry["raw"] = raw
        entry["codepoints_head"] = [hex(ord(c)) for c in raw[:12]]
        # Диагностика mojibake: кириллица в U+0400..U+04FF?
        cyr = sum(1 for c in raw if 0x400 <= ord(c) <= 0x4FF)
        box = sum(1 for c in raw if 0x2500 <= ord(c) <= 0x257F)
        entry["cyrillic_chars"] = cyr
        entry["box_drawing_chars"] = box
        entry["verdict"] = "OK" if cyr > 5 else "MOJIBAKE_OR_ASCII"
        entry["usage"] = {
            "prompt": r.usage.prompt_tokens if r.usage else None,
            "completion": r.usage.completion_tokens if r.usage else None,
        }
    except Exception as e:
        entry["error"] = f"{type(e).__name__}: {str(e)[:300]}"
        entry["verdict"] = "ERROR"
    report["checks"][f"ru:{model}"] = entry
    print(safe(model, entry.get("verdict")), "cyr=", entry.get("cyrillic_chars"),
          "box=", entry.get("box_drawing_chars"))


# ---------- 3. function calling: ошибка или тихое игнорирование? ----------
TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get weather for a city",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}
for model in ["antigravity-3.8-flash", "gpt-4o"]:
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Weather in Kyiv? Use the tool."}],
            tools=[TOOL],
            max_tokens=200,
        )
        m = r.choices[0].message
        report["checks"][f"tools:{model}"] = {
            "returned_tool_calls": bool(m.tool_calls),
            "finish_reason": r.choices[0].finish_reason,
            "content": (m.content or "")[:200],
        }
        print(safe(f"tools:{model}", report["checks"][f"tools:{model}"]))
    except Exception as e:
        report["checks"][f"tools:{model}"] = {"error": f"{type(e).__name__}: {str(e)[:300]}"}
        print(safe(f"tools:{model} ERROR", report["checks"][f"tools:{model}"]["error"]))


# ---------- 4. Стриминг ----------
try:
    chunks = []
    for c in client.chat.completions.create(
        model="antigravity-3.8-flash",
        messages=[{"role": "user", "content": "Count: 1 2 3 4 5"}],
        stream=True,
        max_tokens=100,
    ):
        if c.choices and c.choices[0].delta.content:
            chunks.append(c.choices[0].delta.content)
    text = "".join(chunks)
    report["checks"]["streaming"] = {
        "ok": bool(text),
        "text": text,
        "codepoints_head": [hex(ord(ch)) for ch in text[:12]],
    }
    print(safe("streaming", report["checks"]["streaming"]["ok"]),
          "chars=", len(text))
except Exception as e:
    report["checks"]["streaming"] = {"error": f"{type(e).__name__}: {str(e)[:300]}"}
    print(safe("streaming ERROR", report["checks"]["streaming"]["error"]))


# ---------- 5. Кто ты? (аутентичность модели) ----------
for model in ["antigravity-3.8-flash", "claude-3-5-sonnet-20241022", "gpt-4o"]:
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "user",
                       "content": "Reply with ONLY your exact model name. Nothing else."}],
            max_tokens=60,
        )
        report["checks"][f"who:{model}"] = (r.choices[0].message.content or "")[:150]
        print(safe(f"who:{model}", report["checks"][f"who:{model}"]))
    except Exception as e:
        report["checks"][f"who:{model}"] = f"ERROR {type(e).__name__}"
        print(safe(f"who:{model} ERROR", type(e).__name__))


with open(OUT, "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)

print(f"\nReport written: {OUT}")