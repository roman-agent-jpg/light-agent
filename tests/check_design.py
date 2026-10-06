"""Сверка редизайна: проверяем, что ключевые элементы Claude Code
присутствуют в CSS/JS/HTML, а палитра совпадает с оригиналом.

Запуск:  python tests/check_design.py
"""
import re
from pathlib import Path

WEB = Path("web")
css = (WEB / "static" / "app.css").read_text(encoding="utf-8")
js = (WEB / "static" / "app.js").read_text(encoding="utf-8")
html = (WEB / "index.html").read_text(encoding="utf-8")

lines: list[str] = []
ok = bad = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global ok, bad
    if cond:
        ok += 1
        lines.append(f"OK   {label}" + (f"  — {detail}" if detail else ""))
    else:
        bad += 1
        lines.append(f"FAIL {label}" + (f"  — {detail}" if detail else ""))


# ---------- 1. Палитра из оригинала ----------
lines.append("=== ПАЛИТРА (извлечена из бандла Claude Code 2.1.289) ===")
PALETTE = {
    "--acc": "#d77757",     # терракотовый акцент
    "--bg": "#141413",      # тёплый чёрный
    "--txt": "#e2e8f0",     # светлый текст
    "--dim": "#64748b",     # приглушённый
    "--warn": "#f59e0b",    # жёлтый
    "--err": "#ef4444",     # красный
}
for var, want in PALETTE.items():
    m = re.search(rf'{var}\s*:\s*([^;]+);', css)
    got = (m.group(1).strip() if m else "НЕТ")
    # В переменной могут быть комментарии - отрезаем
    got_clean = got.split("/*")[0].strip()
    check(f"{var} = {want}", got_clean.lower() == want.lower(),
          f"получено {got_clean}")

# ---------- 2. Роли инструментов ----------
lines.append("")
lines.append("=== РОЛИ ИНСТРУМЕНТОВ ===")
check("есть --tool-read", "--tool-read" in css)
check("есть --tool-write", "--tool-write" in css)
check("есть --tool-edit", "--tool-edit" in css)
check("есть --tool-bash", "--tool-bash" in css)
check("есть --tool-search", "--tool-search" in css)
check("цвета привязаны к .act.r-*",
      all(f".act.r-{r}" in css for r in ("read", "write", "edit", "bash", "search")))

TOOLS = ["read_file", "write_file", "edit_file", "run_python_file",
         "execute_code", "make_dir", "move_file", "grep", "web_search",
         "fetch_url", "view_image", "tree", "delete_file", "delete_dir",
         "list_files", "cloud_upload"]
missing = [t for t in TOOLS if f"{t}:" not in js]
check("все инструменты раскрашены", not missing, str(missing))

check("подписи в стиле Claude (Read/Write/Edit/Bash)",
      all(f"'{l}'" in js for l in ("Read", "Write", "Edit", "Bash", "Grep", "Search")))

# ---------- 3. Элементы интерфейса оригинала ----------
lines.append("")
lines.append("=== ЭЛЕМЕНТЫ ОРИГИНАЛА ===")
check("счётчик действий (.cnt)", "cnt" in css and "cnt" in js)
check("мигающий курсор (.caret)", "caret" in css and "caret" in js)
check("diff-подсветка добавления", ".diff-add" in css and "diff-add" in js)
check("diff-подсветка удаления", ".diff-del" in css and "diff-del" in js)
check("поддержка блока ```diff", "```diff" in js)
check("моноширинный в действиях", ".act{" in css and "var(--mono)" in css)
check("марка-логотип (градиент)", "linear-gradient" in css and ".mark" in css)
check("welcome с подсказками команд", "hintline" in css and "hintline" in html)

# ---------- 4. Английские названия ----------
lines.append("")
lines.append("=== НАЗВАНИЯ ===")
check("заголовок Agent", ">Agent<" in html)
check("title на английском", "<title>Agent" in html)
check("имя в шапке", "Agent" in html)
check("бейджи с моноширинным", ".badge{" in css)

# ---------- 5. Мобильность сохранена ----------
lines.append("")
lines.append("=== МОБИЛЬНОСТЬ НЕ СЛОМАНА ===")
check("есть брейкпоинт 700px", "@media(max-width:700px)" in css)
check("есть брейкпоинт 1000px", "@media(max-width:1000px)" in css)
check("есть безопасные зоны", "safe-area-inset" in css)
check("есть prefers-reduced-motion", "prefers-reduced-motion" in css)
check("панели-оверлеи на телефоне", "position:fixed" in css)

# ---------- 6. Ничего не потеряно ----------
lines.append("")
lines.append("=== ФУНКЦИОНАЛЬНОСТЬ СОХРАНЕНА ===")
for fn in ("loadTree", "openFile", "saveFile", "delFile", "doUpload",
           "toggleMic", "send", "stopRun", "installApp", "toggleSide",
           "loadSessions", "newSession", "md(", "highlight("):
    check(f"{fn} на месте", fn in js)
check("PWA-регистрация", "serviceWorker" in js)
check("манифест в HTML", 'rel="manifest"' in html)
check("все селекторы используются",
      all(f'id="{i}"' in html for i in ("tree", "chat", "in", "mic", "bSend",
                                        "bStop", "fpanel", "selModel")))

# ---------- Итог ----------
lines.append("")
lines.append("=" * 54)
lines.append(f"ПРОЙДЕНО: {ok}   ПРОВАЛЕНО: {bad}")
if bad:
    lines.append("")
    lines.append("Провалено:")
    for l in lines:
        if l.startswith("FAIL"):
            lines.append("  " + l)
lines.append("=" * 54)

(Path("reports") / "design_check.txt").write_text("\n".join(lines), encoding="utf-8")
print("\n".join(l.encode("ascii", "backslashreplace").decode("ascii") for l in lines))