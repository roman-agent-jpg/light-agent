"""Пробуем разные способы задать переменные на Render.

Render API отвечает 405 на PUT/POST env-vars - похоже, он вообще
не даёт менять переменные через API для этого плана. Проверяем
все варианты и смотрим, какой принимается.

Запуск:  python tests/probe_render_env.py
"""
import json
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import dotenv_values

creds = dotenv_values(Path("credentials.txt"))
KEY = creds.get("RENDER_API_KEY", "")
SERVICE = "srv-db1coonavr4c73b9vbu0"
TEST_KEY = "AG_PROBE_123"
TEST_VAL = "probe-value-xyz"

lines: list[str] = []


def api(method: str, path: str, payload=None):
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(
        f"https://api.render.com/v1{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {KEY}", "Accept": "application/json",
                 **({"Content-Type": "application/json"} if data else {})})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            b = r.read().decode("utf-8")
            return r.status, (json.loads(b) if b else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:250]
    except Exception as e:
        return 0, f"{type(e).__name__}: {str(e)[:150]}"


payload_variants = [
    ("key+value+type", {"key": TEST_KEY, "value": TEST_VAL, "type": "plain"}),
    ("key+value", {"key": TEST_KEY, "value": TEST_VAL}),
    ("key+value+secret", {"key": TEST_KEY, "value": TEST_VAL, "type": "secret"}),
    ("key+value+sync", {"key": TEST_KEY, "value": TEST_VAL, "type": "secret",
                        "sync": False}),
]

lines.append("=== PUT /services/<id>/env-vars ===")
for label, pl in payload_variants:
    code, res = api("PUT", f"/services/{SERVICE}/env-vars", pl)
    lines.append(f"  {label:22} -> {code}  {str(res)[:130]}")
    if code < 300:
        break

lines.append("")
lines.append("=== POST /services/<id>/env-vars ===")
for label, pl in payload_variants:
    code, res = api("POST", f"/services/{SERVICE}/env-vars", pl)
    lines.append(f"  {label:22} -> {code}  {str(res)[:130]}")
    if code < 300:
        break

lines.append("")
lines.append("=== Альтернанативные пути ===")
alt = [
    ("PATCH env-vars", "PATCH", f"/services/{SERVICE}/env-vars",
     {"key": TEST_KEY, "value": TEST_VAL}),
    ("PUT env-vars/<key>", "PUT",
     f"/services/{SERVICE}/env-vars/{TEST_KEY}", {"value": TEST_VAL}),
    ("PATCH сервиса", "PATCH", f"/services/{SERVICE}",
     {"serviceDetails": {}}),
]
for label, method, path, pl in alt:
    code, res = api(method, path, pl)
    lines.append(f"  {label:22} -> {code}  {str(res)[:130]}")

lines.append("")
lines.append("=== Итог ===")
code, cur = api("GET", f"/services/{SERVICE}/env-vars")
if code == 200 and isinstance(cur, list):
    keys = [e.get("key") for e in cur]
    lines.append(f"переменных на сервере: {len(keys)}")
    lines.append(f"есть ли AG_PROBE_123: {'AG_PROBE_123' in keys}")

Path("reports/probe_render_env.txt").write_text("\n".join(lines), encoding="utf-8")
print("written reports/probe_render_env.txt")
print("\n".join(lines))