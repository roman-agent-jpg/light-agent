"""Достаём палитру из настоящего бандла Claude Code.

Первая попытка скачала 27 КБ — это не бандл, а «заглушка» (npm отдаёт
stub-пакет с postinstall-скриптом). Настоящий код лежит в native-бинаре,
который скачивается отдельно. Пробуем достать палитру оттуда.

Запуск:  python tests/fetch_cc_theme2.py
"""
import json
import re
import tarfile
import urllib.request
from pathlib import Path

OUT = Path("reports")
lines: list[str] = []


def show(t: str) -> None:
    print(t.encode("ascii", "backslashreplace").decode("ascii"))


# ---------- 1. Смотрим, что внутри tarball ----------
url = "https://registry.npmjs.org/@anthropic-ai/claude-code/-/claude-code-2.1.289.tgz"
req = urllib.request.Request(url, headers={"User-Agent": "probe"})
with urllib.request.urlopen(req, timeout=180) as r:
    raw = r.read()

lines.append(f"размер: {len(raw)} Б")

try:
    tf = tarfile.open(fileobj=__import__("io").BytesIO(raw))
    members = tf.getnames()
    lines.append(f"файлов в архиве: {len(members)}")
    for m in members[:25]:
        info = tf.getmember(m)
        lines.append(f"  {m}  ({info.size} Б)")
    # Покажем package.json
    for m in members:
        if m.endswith("package.json"):
            data = tf.extractfile(m).read().decode("utf-8", "replace")
            lines.append("")
            lines.append("=== package.json (первые 2000 симв.) ===")
            lines.append(data[:2000])
            break
except Exception as e:
    lines.append(f"не распаковался: {type(e).__name__} {e}")

(OUT / "cc_pkg.txt").write_text("\n".join(lines), encoding="utf-8")
show("written reports/cc_pkg.txt")
show("\n".join(lines[:40]))