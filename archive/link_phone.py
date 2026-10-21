"""Связь с телефоном через GitHub API (без git-клона). Телефон пишет в ветку phone (phone_feed.json, ledger.json); облако пишет в ветку state (signals.json).
У каждой стороны свой писатель — общих записей нет, значит и гонок нет."""
import base64
import json
import os

import requests

from . import config

API = "https://api.github.com"


def _h():
    tok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    return {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"} if tok else {}


def read_json(path: str, branch: str = "phone", session=None):
    """Содержимое JSON-файла из ветки или None (нет файла/сети/токена)."""
    h = _h()
    if not h:
        return None
    try:
        r = (session or requests).get(f"{API}/repos/{config.REPO}/contents/{path}", params={"ref": branch}, headers=h, timeout=25)
        if r.status_code != 200:
            return None
        return json.loads(base64.b64decode(r.json()["content"]).decode("utf-8"))
    except (requests.RequestException, ValueError, KeyError):
        return None


def write_json(path: str, obj, branch: str, message: str = "обновление", session=None) -> bool:
    """Записать JSON в ветку (создаёт ветку phone из main при первом разе)."""
    h = _h()
    if not h:
        return False
    s = session or requests
    url = f"{API}/repos/{config.REPO}/contents/{path}"
    try:
        cur = s.get(url, params={"ref": branch}, headers=h, timeout=25)
        if cur.status_code == 404:
            base = s.get(f"{API}/repos/{config.REPO}/git/ref/heads/main", headers=h, timeout=25)
            if base.status_code == 200 and s.get(f"{API}/repos/{config.REPO}/git/ref/heads/{branch}", headers=h, timeout=25).status_code == 404:
                s.post(f"{API}/repos/{config.REPO}/git/refs", headers=h, timeout=25, json={"ref": f"refs/heads/{branch}", "sha": base.json()["object"]["sha"]})
            cur = s.get(url, params={"ref": branch}, headers=h, timeout=25)
        body = {"message": message, "branch": branch, "content": base64.b64encode(json.dumps(obj, ensure_ascii=False).encode()).decode()}
        if cur.status_code == 200:
            body["sha"] = cur.json()["sha"]
        return s.put(url, headers=h, json=body, timeout=40).status_code in (200, 201)
    except (requests.RequestException, ValueError, KeyError):
        return False
