"""Изолированная проверка: один запрос, свежий процесс, уникальная строка.

Если мост вернёт не ту строку, которую мы отправили, - это баг моста
(аргументы инструмента подменяются или теряются).

Запуск:  python one_shot.py <текст>
"""
import json
import sys

from openai import OpenAI

from app import config

SENT = sys.argv[1] if len(sys.argv) > 1 else "ZZQQ-7731-MARKER"

client = OpenAI(
    api_key=config.AG_API_KEY,
    base_url=config.AG_BASE_URL,
    timeout=120.0,
    max_retries=0,
)

TOOL = {
    "type": "function",
    "function": {
        "name": "store_marker",
        "description": "Store the marker string you are given, unchanged.",
        "parameters": {
            "type": "object",
            "properties": {"marker": {"type": "string"}},
            "required": ["marker"],
        },
    },
}

lines = [f"sent marker: {SENT!r} (len={len(SENT)})"]

try:
    r = client.chat.completions.create(
        model="antigravity-3.8-flash",
        messages=[{
            "role": "user",
            "content": ("Call store_marker with marker set to exactly "
                        + json.dumps(SENT, ensure_ascii=False)
                        + " - copy it character by character, do not modify."),
        }],
        tools=[TOOL],
        max_tokens=200,
    )
    tcs = r.choices[0].message.tool_calls
    if not tcs:
        lines.append("NO TOOL CALLS")
        lines.append(f"content={(r.choices[0].message.content or '')[:200]!r}")
    else:
        raw = tcs[0].function.arguments
        lines.append(f"raw arguments: {raw!r}")
        got = json.loads(raw).get("marker")
        lines.append(f"got marker:   {got!r}")
        lines.append("VERDICT: OK" if got == SENT else "VERDICT: MISMATCH")
except Exception as e:
    lines.append(f"ERROR {type(e).__name__}: {str(e)[:200]}")

with open("reports/one_shot.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("written reports/one_shot.txt")