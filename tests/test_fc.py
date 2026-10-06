"""Проверка: поддерживает ли мост Antigravity native function calling?

Это критический факт для выбора фреймворка агента:
- если tool_calls возвращаются -> подходит OpenAI Agents SDK / Pydantic AI
- если нет -> только code-exec агенты (smolagents CodeAgent)

Запуск:  python test_fc.py
"""
import json

from openai import OpenAI

from app import config

MODELS = [
    "antigravity-3.8-pro",
    "antigravity-3.8-flash",
    "claude-3-5-sonnet-20241022",
    "gpt-4o",
]

TOOLS = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Получить погоду в городе",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "Название города"}},
            "required": ["city"],
        },
    },
}]

client = OpenAI(
    api_key=config.AG_API_KEY,
    base_url=config.AG_BASE_URL,
    timeout=180.0,
    max_retries=1,
)

results = {}
for model in MODELS:
    print(f"\n{'=' * 60}\n{model}\n{'=' * 60}")
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Какая погода в Киеве? Используй инструмент."}],
            tools=TOOLS,
            max_tokens=300,
        )
        msg = r.choices[0].message
        tc = msg.tool_calls
        if tc:
            args = tc[0].function.arguments
            try:
                parsed = json.loads(args)
            except Exception:
                parsed = args
            print(f"FUNCTION CALLING: ДА -> {tc[0].function.name}({parsed})")
            results[model] = "tools"
        else:
            print("FUNCTION CALLING: НЕТ")
            print(f"  content: {(msg.content or '')[:220]}")
            results[model] = "no_tools"
    except Exception as e:
        print(f"ОШИБКА: {type(e).__name__}: {str(e)[:220]}")
        results[model] = "error"

print(f"\n\n{'=' * 60}\nИТОГ\n{'=' * 60}")
for m, v in results.items():
    print(f"  {m:32} {v}")

tools_ok = [m for m, v in results.items() if v == "tools"]
print(f"\nМоделей с function calling: {len(tools_ok)}/{len(MODELS)}")
if tools_ok:
    print(f"Вывод: можно использовать OpenAI Agents SDK. Модели: {', '.join(tools_ok)}")
else:
    print("Вывод: нужен code-exec агент (smolagents).")