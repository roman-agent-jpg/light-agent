"""Достаём реальные цвета и элементы интерфейса из пакета Claude Code.

Claude Code распространяется как npm-пакет @anthropic-ai/claude-code.
В его бандле есть зашитые hex-цвета темы — это точная палитра, а не
догадки по скриншотам.

Запуск:  python tests/fetch_cc_theme.py
"""
import json
import re
import tarfile
import urllib.request
from pathlib import Path

OUT = Path("reports")
OUT.mkdir(exist_ok=True)

PKG = "https://registry.npmjs.org/@anthropic-ai/claude-code/-/claude-code-%s.tgz"

lines: list[str] = []


def show(text: str) -> None:
    print(text.encode("ascii", "backslashreplace").decode("ascii"))


# ---------- 1. Узнаём свежую версию ----------
try:
    with urllib.request.urlopen(
            "https://registry.npmjs.org/@anthropic-ai/claude-code",
            timeout=60) as r:
        meta = json.loads(r.read().decode())
    latest = meta.get("dist-tags", {}).get("latest", "")
    lines.append(f"последняя версия: {latest}")
    tar_url = PKG % latest
except Exception as e:
    lines.append(f"не удалось узнать версию: {type(e).__name__} {e}")
    lines.append("пробуем заведомо существующую версию")
    tar_url = PKG % "2.0.0"

# ---------- 2. Скачиваем архив ----------
raw = None
for attempt in range(3):
    try:
        req = urllib.request.Request(tar_url,
                                     headers={"User-Agent": "probe"})
        with urllib.request.urlopen(req, timeout=180) as r:
            raw = r.read()
        lines.append(f"скачано: {len(raw) // 1024} КБ")
        break
    except Exception as e:
        lines.append(f"попытка {attempt + 1}: {type(e).__name__} {e}")
if raw is None:
    (OUT / "cc_theme.txt").write_text("\n".join(lines), encoding="utf-8")
    show("не удалось скачать пакет")
    raise SystemExit(0)

# ---------- 3. Ищем hex-цвета в бандле ----------
blob = raw.decode("latin-1", "ignore")

# Палитра, характерная для темы: 6 hex подряд (RGB-тройки)
rgb = re.findall(r'#([0-9a-fA-F]{6})', blob)
counts: dict[str, int] = {}
for h in rgb:
    counts[h.lower()] = counts.get(h.lower(), 0) + 1

# Только те, что встречаются много раз: разовые — случайные строки в коде
strong = sorted(((c, h) for h, c in counts.items() if c >= 3),
                reverse=True)

lines.append("")
lines.append(f"всего уникальных цветов: {len(counts)}")
lines.append(f"встречаются 3+ раза: {len(strong)}")
lines.append("")
lines.append("ТОП-40 цветов по частоте (как в оригинале):")
for cnt, h in strong[:40]:
    r_, g_, b_ = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    lines.append(f"  #{h}  rgb({r_},{g_},{b_})   встречается {cnt} раз")

# ---------- 4. Ищем названия ролей для цветов ----------
lines.append("")
lines.append("=== Поиск именованных токенов темы ===")
# В бандле часто идут ANSI-палитры 16 цветов
ansi = re.findall(r'\[(\d{1,3})\]', blob)
lines.append(f"ANSI-кодов найдено: {len(ansi)} (это не цвета, но показывает "
             f"наличие палитры)")

# Ищем названия вроде accent, primary, muted, dim
for token in ("accent", "primary", "muted", "dim", "subtle", "border",
              "success", "warning", "danger", "toolName", "successColor"):
    hits = len(re.findall(rf'"{token}"', blob))
    if hits:
        lines.append(f"  токен {token}: {hits} упоминаний")

# ---------- 5. Ищем текстовые элементы UI ----------
lines.append("")
lines.append("=== Тексты интерфейса оригинала ===")
# Подсказки вроде "Try" и названия режимов
ui_terms = [
    "Try", "Welcome to Claude Code", "cwd:", "git repo", "bypassing permissions",
    "acceptEdits", "plan mode", "auto-accept edits", "shift+tab",
    "esc to interrupt", "Press up to edit", "disallowed",
    "Welcome to", "no changes", "File not found",
    "tokens", "Context left", "Thinking",
]
for t in ui_terms:
    n = blob.count(t)
    if n:
        lines.append(f"  {t!r}: {n}")

(OUT / "cc_theme.txt").write_text("\n".join(lines), encoding="utf-8")
show("written reports/cc_theme.txt")
show("\n".join(lines[:50]))