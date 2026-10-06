"""Проверка состояния Blueprint после Apply в Dashboard.

Показывает: виден ли репозиторий Render, есть ли сервис, что в логах.

Запуск:  python blueprint_check.py
"""
import json
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import dotenv_values

creds = dotenv_values(Path("credentials.txt"))
RKEY = creds.get("RENDER_API_KEY", "")
OWNER = "tea-db1as9ugekts73d041dg"

lines: list[str] = []


def api(path: str) -> tuple[int, object]:
    req = urllib.request.Request(
        f"https://api.render.com/v1{path}",
        headers={"Authorization": f"Bearer {RKEY}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            b = r.read().decode("utf-8")
            return r.status, (json.loads(b) if b else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:400]
    except Exception as e:
        return 0, f"{type(e).__name__}: {str(e)[:200]}"


# 1. Владелец
code, owners = api("/owners")
lines.append(f"GET /owners -> {code}")
if code == 200 and isinstance(owners, list):
    for o in owners:
        lines.append(f"  {json.dumps(o, ensure_ascii=False)[:200]}")
else:
    lines.append(f"  {str(owners)[:300]}")

# 2. Сервисы
code, svcs = api("/services")
lines.append("")
lines.append(f"GET /services -> {code}")
if code == 200 and isinstance(svcs, list):
    lines.append(f"  количество: {len(svcs)}")
    for s in svcs:
        svc = s.get("service", s)
        det = s.get("serviceDetails", {})
        lines.append(f"  - {svc.get('name')} [{svc.get('type')}]")
        lines.append(f"      id={svc.get('id')}")
        lines.append(f"      url={det.get('url')}")
        lines.append(f"      repo={det.get('githubRepo')}")
        lines.append(f"      branch={det.get('branch')}")
        lines.append(f"      state={svc.get('serviceDetails', {}).get('runtime')}")
else:
    lines.append(f"  {str(svcs)[:300]}")

# 3. Попробовать достать конкретный сервис по имени
for name in ("light-agent", "light-agent-1"):
    code, one = api(f"/services/{name}")
    lines.append("")
    lines.append(f"GET /services/{name} -> {code}")
    if code == 200 and isinstance(one, dict):
        lines.append(f"  {json.dumps(one, ensure_ascii=False)[:400]}")

with open("reports/blueprint_check.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("written reports/blueprint_check.txt")
print("\n".join(lines))