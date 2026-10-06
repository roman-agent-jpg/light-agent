"""Палитра из на��тоящего бандла Claude Code (linux-x64 native-пакет).

npm-пает @anthropic-ai/claude-code — тонкая обёртка, а весь UI-код
лежит в @anthropic-ai/claude-code-linux-x64. Там и ищем цвета.

Запуск:  python tests/fetch_cc_theme3.py
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
PKGS = [
    f"https://registry.npmjs.org/@anthropic-ai/claude-code-linux-x64/-/claude-code-linux-x64-{VER}.tgz",
    f"https://registry.npmjs.org/@anthropic-ai/claude-code-linux-x64/-/claude-code-linux-x64-{VER}.tgz",
]

blob = None
for url in PKGS:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "probe"})
        with urllib.request.urlopen(req, timeout=300) as r:
            data = r.read()
        lines.append(f"скачали {url.split('/')[-1]}: {len(data)//1024//1024} МБ")
        tf = tarfile.open(fileobj=io.BytesIO(data))
        lines.append("")
        lines.append("=== содержимое архива ===")
        for name in tf.getnames():
            info = tf.getmember(name)
            lines.append(f"  {name}  ({info.size//1024//1024} МБ)")
        # Ищем самый большой файл - почти наверняка это бандл,
        # а расширение может быть любым (.node, .bin, без расширения)
        best = None
        for name in tf.getnames():
            info = tf.getmember(name)
            if not info.isfile():
                continue
            if best is None or info.size > tf.getmember(best).size:
                best = name
        if best:
            size_mb = tf.getmember(best).size // 1024 // 1024
            lines.append("")
            lines.append(f"берём самый большой файл: {best} ({size_mb} МБ)")
            blob = tf.extractfile(best).read().decode("utf-8", "ignore")
        if blob:
            break
    except Exception as e:
        lines.append(f"{url.split('/')[-1]}: {type(e).__name__} {e}")

if not blob:
    lines.append("бандл не найден")
    (OUT / "cc_palette.txt").write_text("\n".join(lines), encoding="utf-8")
    show("\n".join(lines))
    raise SystemExit(0)

lines.append(f"размер бандла: {len(blob)//1024} КБ")

# ---------- Палитра ----------
counts: dict[str, int] = {}
for h in re.findall(r'#([0-9a-fA-F]{6})\b', blob):
    counts[h.lower()] = counts.get(h.lower(), 0) + 1
for h in re.findall(r'#([0-9a-fA-F]{3})\b', blob):
    counts[h.lower()] = counts.get(h.lower(), 0) + 1

strong = sorted(((c, h) for h, c in counts.items() if c >= 4), reverse=True)
lines.append("")
lines.append(f"уникальных цветов: {len(counts)}, встречаются 4+ раз: {len(strong)}")
lines.append("")
lines.append("ТОП-45 цветов оригинала Claude Code:")
for cnt, h in strong[:45]:
    if len(h) == 6:
        r_, g_, b_ = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        rgb = f"rgb({r_:3},{g_:3},{b_:3})"
    else:
        rgb = ""
    lines.append(f"  #{h:<8} {rgb:18} x{cnt}")

# ---------- Как выглядят именованные роли ----------
lines.append("")
lines.append("=== Именованные токены темы ===")
for token in ("accent", "primary", "secondary", "muted", "dim", "subtle",
              "border", "success", "warning", "danger", "error", "toolUse",
              "toolResult", "permissionMode", "diffAdded", "diffRemoved",
              "orange", "coral", "claude"):
    n = len(re.findall(rf'"{token}"', blob))
    if n:
        lines.append(f"  {token}: {n}")

# ---------- Элементы интерфейса ----------
lines.append("")
lines.append("=== Тексты и элементы UI ===")
ui = [
    "Welcome to Claude Code", "Try", "cwd:", "bypass permissions",
    "acceptEdits", "plan", "auto", "esc to interrupt", "shift+tab",
    "Press up", "Thinking", "tokens", "Context left", "unspecified",
    "Do you want", "Yes", "No", "tab to", "ctrl+",
]
for t in ui:
    n = blob.count(t)
    if n:
        lines.append(f"  {t!r}: {n}")

(OUT / "cc_palette.txt").write_text("\n".join(lines), encoding="utf-8")
show("written reports/cc_palette.txt")
show("\n".join(lines))