"""Проверка: поддерживает ли мост вызов инструментов через текстовый протокол."""
import json

from openai import OpenAI

from app import config

c = OpenAI(api_key=config.AG_API_KEY, base_url=config.AG_BASE_URL, timeout=180)

SYS = """Ты автономный агент. У тебя есть инструменты.

Вызывай инструмент СТРОГО в таком формате, без markdown и без пояснений:
<TOOL>имя_инструмента</TOOL>
<ARGS>{"параметр": "значение"}</ARGS>

Один вызов за раз. Дождись результата, потом решай что делать дальше.
Когда задача полностью выполнена, начни ответ со слова ГОТОВО и опиши результат.

Доступные инструменты:
- write_file(path, content) — создать текстовый файл
- read_file(path) — прочитать файл
- execute_code(code) — выполнить Python-код
- list_files() — список файлов
"""

msgs = [
    {"role": "system", "content": SYS},
    {"role": "user", "content": "Создай файл test.txt с текстом привет и затем прочитай его."},
]

r = c.chat.completions.create(
    model="antigravity-3.8-flash", messages=msgs, max_tokens=900,
)
out = r.choices[0].message.content
print("RAW OUTPUT:")
print(out)
print("=" * 50)
print("contains <TOOL>:", "<TOOL>" in out)

# Если протокол соблюдён — попробуем распарсить и выполнить
if "<TOOL>" in out:
    try:
        name = out.split("<TOOL>")[1].split("</TOOL>")[0].strip()
        raw_args = out.split("<ARGS>")[1].split("</ARGS>")[0].strip()
        print("parsed tool:", name)
        print("parsed args:", json.loads(raw_args))
    except Exception as exc:
        print("parse failed:", exc)