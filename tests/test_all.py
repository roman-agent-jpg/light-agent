"""Тесты всего приложения локально. Запуск:  python -m pytest tests -q
или просто:  python tests/test_all.py
"""
import io
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app import agent, config, main, memory, tools  # noqa: E402

WS = config.WORKSPACE
PASS, FAIL = [], []

# Токен админа для проверки защищённых ручек. Подставляем в конфиг,
# чтобы тесты не зависели от того, что задано в окружении на машине.
TEST_TOKEN = "test-admin-token-for-checks"
config.ADMIN_TOKEN = TEST_TOKEN


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    mark = "OK  " if cond else "FAIL"
    print(f"{mark} {name}" + (f"  — {detail}" if detail and not cond else ""))


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main_test() -> int:
    c = TestClient(main.app)

    # Чистим рабочую папку перед тестами
    for name in ("t_dir", "t_pic.png"):
        p = WS / name
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.is_file():
            p.unlink()

    # ---------- Базовые ----------
    section("Базовые маршруты")
    r = c.get("/health")
    check("GET /health", r.status_code == 200 and r.json()["status"] == "ok",
          str(r.status_code))

    r = c.get("/")
    check("GET / (интерфейс)", r.status_code == 200 and "<!DOCTYPE" in r.text,
          str(r.status_code))

    r = c.get("/static/app.css")
    check("GET /static/app.css", r.status_code == 200, str(r.status_code))
    r = c.get("/static/app.js")
    check("GET /static/app.js", r.status_code == 200, str(r.status_code))

    # ---------- PWA ----------
    section("PWA (установка как приложение)")
    r = c.get("/manifest.json")
    ok_m = r.status_code == 200
    check("GET /manifest.json", ok_m, str(r.status_code))
    if ok_m:
        check("Content-Type манифеста",
              "manifest" in r.headers.get("content-type", ""),
              r.headers.get("content-type", "?"))
        mf = r.json()
        check("manifest: display standalone",
              mf.get("display") == "standalone", str(mf.get("display")))
        check("manifest: есть name и short_name",
              bool(mf.get("name")) and bool(mf.get("short_name")))
        check("manifest: start_url и scope",
              mf.get("start_url") == "/" and mf.get("scope") == "/")
        icons = mf.get("icons", [])
        check("manifest: есть иконка 192",
              any(i.get("sizes") == "192x192" for i in icons), str(len(icons)))
        check("manifest: есть иконка 512",
              any(i.get("sizes") == "512x512" for i in icons))
        check("manifest: есть maskable (Android)",
              any(i.get("purpose") == "maskable" for i in icons))
        check("manifest: theme_color задан", bool(mf.get("theme_color")))
        check("manifest: есть шорткаты", len(mf.get("shortcuts", [])) >= 3)

    r = c.get("/static/sw.js")
    check("GET /static/sw.js", r.status_code == 200, str(r.status_code))
    if r.status_code == 200:
        sw = r.text
        check("sw: регистрирует install/activate",
              "addEventListener('install'" in sw and
              "addEventListener('activate'" in sw)
        check("sw: перехватывает fetch", "addEventListener('fetch'" in sw)
        check("sw: НЕ кэширует API", "/api/" in sw and "return;" in sw)
        check("sw: есть fallback для офлайна", "Нет связи" in sw)

    r = c.get("/static/icons/icon-192.png")
    check("иконка 192 отдаётся", r.status_code == 200 and
          r.headers.get("content-type") == "image/png",
          f"{r.status_code} {r.headers.get('content-type')}")

    r = c.get("/static/icons/icon-512.png")
    check("иконка 512 отдаётся", r.status_code == 200, str(r.status_code))

    r = c.get("/static/icons/maskable-512.png")
    check("maskable-иконка отдаётся", r.status_code == 200, str(r.status_code))

    r = c.get("/")
    check("Service-Worker-Allowed заголовок",
          r.headers.get("service-worker-allowed") == "/",
          str(r.headers.get("service-worker-allowed")))
    check("Cache-Control: no-cache на главной",
          "no-cache" in r.headers.get("cache-control", ""),
          str(r.headers.get("cache-control")))
    html = r.text
    check("в HTML есть link на манифест",
          'rel="manifest"' in html)
    check("в HTML есть apple-touch-icon",
          "apple-touch-icon" in html)
    check("в HTML viewport-fit=cover (безопасные зоны)",
          "viewport-fit=cover" in html)
    check("в HTML есть кнопка установки", 'id="bInstall"' in html)

    r = c.get("/api/status")
    d = r.json() if r.status_code == 200 else {}
    check("GET /api/status", r.status_code == 200 and "providers" in d,
          str(r.status_code))
    check("в статусе есть список инструментов",
          len(d.get("tools", [])) >= 15, f"tools={len(d.get('tools', []))}")

    # ---------- Файлы ----------
    section("Файловый API")
    check("POST /api/mkdir", c.post("/api/mkdir",
                                    json={"path": "t_dir"}).status_code == 200)
    check("POST /api/mkdir (вложенная)",
          c.post("/api/mkdir", json={"path": "t_dir/sub"}).status_code == 200)

    r = c.put("/api/file", json={"path": "t_dir/a.txt", "content": "привет\n"})
    check("PUT /api/file", r.status_code == 200 and r.json()["size"] > 0,
          str(r.status_code))

    r = c.get("/api/file", params={"path": "t_dir/a.txt"})
    check("GET /api/file", r.status_code == 200 and r.json()["content"] == "привет\n",
          str(r.status_code))

    r = c.get("/api/tree", params={"depth": 3})
    names = []
    def collect(items):
        for i in items:
            names.append(i["name"])
            collect(i.get("children", []))
    if r.status_code == 200:
        collect(r.json()["tree"])
    check("GET /api/tree отдаёт вложенность",
          r.status_code == 200 and "t_dir" in names and "a.txt" in names,
          str(names))

    r = c.post("/api/upload", files={
        "file": ("t_pic.png", io.BytesIO(b"\x89PNG\r\n\x1a\nfake"), "image/png")})
    check("POST /api/upload", r.status_code == 200 and r.json()["is_image"],
          str(r.status_code))

    r = c.get("/api/file", params={"path": "t_pic.png"})
    check("картинка помечается is_image", r.status_code == 200 and
          r.json().get("is_image") is True, str(r.status_code))

    r = c.get("/api/raw", params={"path": "t_pic.png"})
    check("GET /api/raw отдаёт картинку",
          r.status_code == 200 and r.headers["content-type"] == "image/png",
          f"{r.status_code} {r.headers.get('content-type')}")

    r = c.get("/api/raw", params={"path": "t_dir/a.txt"})
    check("GET /api/raw отдаёт текст",
          r.status_code == 200 and "text" in r.headers["content-type"],
          r.headers.get("content-type", "?"))

    r = c.post("/api/rename", json={"source": "t_dir/a.txt",
                                    "destination": "t_dir/b.txt"})
    check("POST /api/rename", r.status_code == 200, str(r.status_code))

    # ---------- Безопасность ----------
    section("Безопасность")
    for bad in ("../../etc/passwd", "..\\windows\\system32", "/etc/hosts"):
        r = c.get("/api/file", params={"path": bad})
        check(f"выход за пределы: {bad}", r.status_code == 400,
              f"получен {r.status_code}")

    r = c.post("/admin/cleanup", json={"dry_run": True},
               headers={"Authorization": "Bearer wrong"})
    check("уборка без прав -> 403", r.status_code == 403, str(r.status_code))

    r = c.post("/admin/cleanup", json={"dry_run": True})
    check("уборка без токена -> 401", r.status_code == 401, str(r.status_code))

    # ---------- Инструменты ----------
    section("Инструменты агента")
    specs = tools.build_tools()
    check("инструментов >= 15", len(specs) >= 15, str(len(specs)))
    bad = [t["function"]["name"] for t in specs
           if not t["function"].get("description")
           or t["function"]["description"][0].islower()]
    check("все описания читаемы", not bad, str(bad))

    check("tool_write_file", "t_dir" in tools.execute("write_file", {
        "path": "t_dir/w.txt", "content": "alpha\nbeta\ngamma\n"}))
    check("tool_read_file", "alpha" in tools.execute(
        "read_file", {"path": "t_dir/w.txt"}))

    r = tools.execute("edit_file", {"path": "t_dir/w.txt",
                                    "old_text": "beta", "new_text": "BETA"})
    check("tool_edit_file", "Изменён" in r, r)
    check("правка реально применилась", "BETA" in tools.execute(
        "read_file", {"path": "t_dir/w.txt"}))

    # Промах: ищем опечатку "alpba" — рядом в файле есть "alpha"
    r = tools.execute("edit_file", {"path": "t_dir/w.txt",
                                    "old_text": "alpba", "new_text": "x"})
    check("edit_file подсказывает похожую строку при опечатке",
          "Ближайшая строка" in r, r)
    check("edit_file сообщает о промахе", "не найден" in r.lower(), r)
    check("после промаха файл не изменён", "BETA" in tools.execute(
        "read_file", {"path": "t_dir/w.txt"}))

    r = tools.execute("grep", {"pattern": "BETA"})
    check("tool_grep находит", "t_dir/w.txt" in r, r[:120])

    r = tools.execute("grep", {"pattern": "НЕТТАКОГО"})
    check("tool_grep сообщает «не найдено»", "Ничего не найдено" in r, r[:80])

    r = tools.execute("view_image", {"path": "t_pic.png"})
    check("tool_view_image", "Картинка" in r, r[:80])

    r = tools.execute("tree", {"path": ".", "max_depth": 2})
    check("tool_tree", "t_dir" in r, r[:80])

    r = tools.execute("make_dir", {"path": "t_dir/made"})
    check("tool_make_dir", "Создана папка" in r, r)

    r = tools.execute("move_file", {"source": "t_dir/w.txt",
                                    "destination": "t_dir/moved.txt"})
    check("tool_move_file", "Перемещено" in r, r)

    r = tools.execute("нет такого", {})
    check("неизвестный инструмент", "Неизвестный" in r, r)

    r = tools.execute("read_file", {"path": "../../../etc/passwd"})
    check("инструмент защищён от выхода", "Недопустимый путь" in r, r[:80])

    # ---------- Память ----------
    section("Память")
    s = "test-sessions"
    memory.clear_session(s)
    r = c.get("/api/sessions")
    check("GET /api/sessions", r.status_code == 200 and "sessions" in r.json(),
          str(r.status_code))

    r1 = agent.run("Мой проект называется Орион. Запомни.", s)
    r2 = agent.run("Как называется мой проект? Ответь одним словом.", s)
    check("память работает", r2.get("ok") and "Орион" in str(r2.get("answer", "")),
          str(r2.get("answer"))[:80])

    # ---------- Отмена ----------
    section("Отмена")
    check("agent.cancel на пустой сессии -> False",
          agent.cancel("нет-такой-сессии") is False)
    check("agent.state содержит running_jobs",
          "running_jobs" in agent.state(), str(agent.state()))

    # ---------- SSE ----------
    section("SSE-стриминг")
    with c.stream("POST", "/api/chat/stream",
                  json={"message": "Назови столицу Франции одним словом",
                        "session_id": "test-sse"}) as resp:
        body = ""
        for chunk in resp.iter_text():
            body += chunk
            if "event: done" in body:
                break
    check("SSE отдаёт событие open", "event: open" in body, body[:120])
    check("SSE отдаёт финальный ответ", "event: done" in body, body[:200])

    # ---------- Уборка ----------
    section("Уборка")
    r = c.post("/api/reset", json={"session_id": s})
    check("POST /api/reset", r.status_code == 200, str(r.status_code))

    # ---------- Секреты и инфраструктура ----------
    section("Секреты и инфраструктура")
    from app import infra, vault

    st = infra.status()
    check("хранилище секретов доступно", st["vault"]["enabled"], str(st))
    check("ключ Render на месте", st["render"], str(st))
    check("Management API настроен", st["management_api"], str(st))

    # Шифрование
    probe = "rnd_probe_value_9911"
    enc = vault.encrypt(probe)
    check("шифрование обратимо", vault.decrypt(enc) == probe)
    check("шифротекст не содержит исходного", probe not in enc)
    check("шифротекст — Fernet", enc.startswith("gAAAA"), enc[:20])
    check("одинаковые значения шифруются по-разному",
          vault.encrypt("x") != vault.encrypt("x"))

    # Валидация имён
    for bad_name in ("mykey", "ADMIN_TOKEN", "плохое имя", ""):
        valid, _ = vault._valid_name(bad_name)
        check(f"имя отклонено: {bad_name or '(пусто)'}", not valid)
    check("имя RENDER_API_KEY принято", vault._valid_name("RENDER_API_KEY")[0])

    # CRUD через API
    SNAME = "RENDER_TEST_KEY"
    vault.delete_secret(SNAME)
    r = c.post("/admin/vault", json={"name": SNAME, "value": probe,
                                     "note": "тест"},
               headers={"Authorization": "Bearer " + TEST_TOKEN})
    check("POST /admin/vault", r.status_code == 200, r.text[:120])

    r = c.get("/api/vault")
    names = [s["name"] for s in r.json().get("secrets", [])]
    check("GET /api/vault показывает секрет", SNAME in names, str(names))
    check("в ответе нет значений",
          all("value" not in s for s in r.json().get("secrets", [])))

    # В базе лежит шифротекст
    client = vault._get_client()
    if client is not None:
        raw = (client.table(vault.SECRETS_TABLE)
               .select("value").eq("name", SNAME).limit(1).execute())
        stored = (raw.data or [{}])[0].get("value", "")
        check("в базе шифротекст, а не открытый ключ",
              probe not in stored and stored.startswith("gAAAA"))

    r = c.post("/admin/vault", json={"name": "mykey", "value": "x"},
               headers={"Authorization": "Bearer " + TEST_TOKEN})
    check("имя без префикса отклонено", r.status_code == 400, str(r.status_code))

    r = c.post("/admin/vault", json={"name": SNAME, "value": "x"})
    check("vault без токена -> 401", r.status_code == 401, str(r.status_code))

    r = c.delete("/admin/vault", params={"name": SNAME},
                 headers={"Authorization": "Bearer " + TEST_TOKEN})
    check("DELETE /admin/vault", r.status_code == 200, r.text[:100])
    r = c.get("/api/vault")
    check("после удаления секрета нет",
          SNAME not in [s["name"] for s in r.json().get("secrets", [])])

    # Защита http_request
    for bad_url in ("http://api.render.com/v1/services",
                    "https://localhost/admin",
                    "https://169.254.169.254/latest/meta-data/"):
        out = infra.tool_http_request("GET", bad_url)
        check(f"http_request блокирует {bad_url[:34]}",
              "Только https" in out or "заблокирован" in out, out[:60])

    # Инструменты агента
    spec_names = [t["function"]["name"] for t in tools.build_tools()]
    for need in ("secret_set", "secret_list", "secret_delete",
                 "infra_services", "infra_env", "infra_deploy",
                 "infra_deploy_status", "infra_logs", "infra_create",
                 "http_request"):
        check(f"инструмент {need} зарегистрирован", need in spec_names)
        check(f"{need} в реестре вызовов", need in tools._REGISTRY)

    check("инструментов >= 26", len(spec_names) >= 26, str(len(spec_names)))

    # ---------- Уборка ----------
    shutil.rmtree(WS / "t_dir", ignore_errors=True)
    (WS / "t_pic.png").unlink(missing_ok=True)

    # ---------- Итог ----------
    print("\n" + "=" * 52)
    print(f"ПРОЙДЕНО: {len(PASS)}   ПРОВАЛЕНО: {len(FAIL)}")
    if FAIL:
        print("\nПровалено:")
        for f in FAIL:
            print(f"  - {f}")
    print("=" * 52)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main_test())