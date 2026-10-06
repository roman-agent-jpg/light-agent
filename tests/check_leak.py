"""Проверка: нет ли РЕАЛЬНЫХ секретов в коммите.

.env.example коммитить можно - это шаблон с пустыми значениями.
Проверяем именно значения ключей.

Запуск:  python check_leak.py
"""
import subprocess
from pathlib import Path

from dotenv import dotenv_values

creds = dotenv_values(Path("credentials.txt"))
SECRETS = {k: v for k, v in creds.items() if v and len(v) > 12}

lines = [f"проверяю {len(SECRETS)} секретов из credentials.txt", ""]


def git(*args: str) -> str:
    p = subprocess.run(["git", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.stdout + p.stderr


# Все файлы в коммите
files = git("ls-files").split()
lines.append(f"файлов в репозитории: {len(files)}")

# Ищем секреты в рабочем дереве (то, что попадёт в сборку)
leaks = []
for f in files:
    p = Path(f)
    if not p.is_file():
        continue
    try:
        content = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        continue
    for name, value in SECRETS.items():
        if value and value in content:
            leaks.append((f, name))

lines.append("")
if leaks:
    lines.append("!!! УТЕЧКИ:")
    for f, name in leaks:
        lines.append(f"    {f} содержит {name}")
else:
    lines.append("OK: реальных секретов в файлах репозитория нет")

# Проверяем .env.example - там должны быть пустые значения
ex = Path(".env.example")
if ex.is_file():
    lines.append("")
    lines.append("проверка .env.example (шаблон):")
    d = dotenv_values(ex)
    for k in ("SUPABASE_KEY", "S3_SECRET_KEY", "RENDER_API_KEY"):
        v = (d.get(k) or "")
        status = "EMPTY (ок)" if not v else f"НЕ ПУСТОЙ! len={len(v)}"
        lines.append(f"    {k:20} {status}")

# Проверяем .gitignore
lines.append("")
gi = Path(".gitignore")
if gi.is_file():
    txt = gi.read_text(encoding="utf-8", errors="ignore")
    for must in (".env", "credentials"):
        ok = must in txt
        lines.append(f"    .gitignore содержит '{must}': {ok}")

with open("reports/check_leak.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("written reports/check_leak.txt")