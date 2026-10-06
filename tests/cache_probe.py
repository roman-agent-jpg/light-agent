"""Проверка гипотезы кэша в мосте.

В cyr_probe.py все 7 разных маркеров вернули ОДНО И ТО ЖЕ значение -
последний маркер в списке. Похоже, мост кэширует аргументы инструмента
и отдаёт результат предыдущего запроса.

Если гипотеза верна, при обратном порядке все ответят на новый последний маркер.

Запуск:  python cache_probe.py
"""
import json

from openai import OpenAI

from app import config

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
        "description": "Store the marker string unchanged.",
        "parameters": {
            "type": "object",
            "properties": {"marker": {"type": "string"}},
            "required": ["marker"],
        },
    },
}

# Уникальные ASCII-маркеры: PowerShell их не портит.
MARKERS = ["AAA111", "BBB222", "CCC333", "DDD444", "EEE555"]

lines = ["FORWARD ORDER (последний = EEE555)"]
for m in MARKERS:
    r = client.chat.completions.create(
        model="antigravity-3.8-flash",
        messages=[{"role": "user", "content":
                   "Call store_marker with marker exactly " + m +
                   ", copy exactly, do not modify."}],
        tools=[TOOL],
        max_tokens=100,
    )
    tcs = r.choices[0].message.tool_calls
    got = json.loads(tcs[0].function.arguments).get("marker") if tcs else None
    lines.append(f"  sent={m:8} got={got!r:12} {'OK' if got == m else '<<< MISMATCH'}")

lines.append("")
lines.append("REVERSE ORDER (последний = AAA111)")
for m in reversed(MARKERS):
    r = client.chat.completions.create(
        model="antigravity-3.8-flash",
        messages=[{"role": "user", "content":
                   "Call store_marker with marker exactly " + m +
                   ", copy exactly, do not modify."}],
        tools=[TOOL],
        max_tokens=100,
    )
    tcs = r.choices[0].message.tool_calls
    got = json.loads(tcs[0].function.arguments).get("marker") if tcs else None
    lines.append(f"  sent={m:8} got={got!r:12} {'OK' if got == m else '<<< MISMATCH'}")

with open("reports/cache_probe.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("written reports/cache_probe.txt")