"""Ищем в бандле Claude Code, как выглядят роли toolUse/diff и тексты приветствия.

Запуск:  python tests/fetch_cc_ui.py
"""
import io
import re
import tarfile
import urllib.request
from pathlib import Path

OUT = Path("reports")
lines: list[str] = []


def show(t: str) -> None:
    print(t.encode("ascii", "backslashreplace").decode("ascii"))


VER = "2.1.289"
url = (f"https://registry.npmjs.org/@anthropic-ai/claude-code-linux-x64/-/"
       f"claude-code-linux-x64-{VER}.tgz")
req = urllib.request.Request(url, headers={"User-Agent": "probe"})
with urllib.request.urlopen(req, timeout=300) as r:
    data = r.read()

tf = tarfile.open(fileobj=io.BytesIO(data))
blob = tf.extractfile("package/claude").read().decode("utf-8", "ignore")
lines.append(f"бандл: {len(blob)//1024} КБ")

# ---------- 1. Контекст вокруг toolUse / diffAdded ----------
lines.append("")
lines.append("=== Контекст вокруг ролей оформления ===")
for token in ("toolUse", "diffAdded", "diffRemoved", "permissionMode"):
    lines.append("")
    lines.append(f"--- {token} ---")
    seen = 0
    for m in re.finditer(rf'"{token}"', blob):
        s = max(0, m.start() - 260)
        chunk = blob[s:m.end() + 260].replace("\n", " ")
        # Показываем только если рядом есть что-то похожее на тему/цвет
        if "color" in chunk.lower() or "theme" in chunk.lower() or "#" in chunk:
            lines.append("  " + chunk[:520])
            seen += 1
            if seen >= 3:
                break
    if not seen:
        lines.append("  (рядом нет упоминаний цвета/темы)")

# ---------- 2. Тексты приветствия ----------
lines.append("")
lines.append("=== Тексты вокруг 'Welcome to Claude Code' ===")
for m in re.finditer(r'Welcome to Claude Code', blob):
    s = max(0, m.start() - 400)
    lines.append("  " + blob[s:m.end() + 700].replace("\n", " ")[:1100])
    lines.append("")

# ---------- 3. Подсказки "Try" ----------
lines.append("=== Строки с 'Try ' (подсказки) ===")
found = set()
for m in re.finditer(r'"Try [^"]{3,70}"', blob):
    found.add(m.group(0))
for s in sorted(found)[:25]:
    lines.append("  " + s)

# ---------- 4. Режимы разрешений ----------
lines.append("")
lines.append("=== Режимы разрешений ===")
for m in re.finditer(r'"(bypass permissions|plan mode|acceptEdits[^"]{0,40})"', blob):
    lines.append("  " + m.group(1))

# ---------- 5. Фразы интерфейса ----------
lines.append("")
lines.append("=== Фразы интерфейса (для точного текста) ===")
phrases = [
    "esc to interrupt", "Press up to edit", "shift+tab", "Thinking",
    "tokens", "Do you want to", "Yes, and", "No, and", "Auto-accept",
    "bypassing permissions", "not be updated", "wrote",
    "File created", "Updated", "Read", "Write", "Edit",
]
for p in phrases:
    n = blob.count(p)
    if n:
        lines.append(f"  {p!r}: {n}")

(OUT / "cc_ui.txt").write_text("\n".join(lines), encoding="utf-8")
show("written reports/cc_ui.txt")
show("\n".join(lines[:70]))