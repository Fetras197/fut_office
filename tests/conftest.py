import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["FO_STATE"] = str(ROOT / ".test_state")
for k in ("TG_TOKEN", "TG_CHAT", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY", "MISTRAL_API_KEY", "GH_TOKEN", "GITHUB_TOKEN", "OFFICE_HALT"):
    os.environ.pop(k, None)

from office import db as dbm, http  # noqa: E402


@pytest.fixture
def db(tmp_path):
    http._pause.clear()
    http._last.clear()
    return dbm.DB(tmp_path / "t.db")


@pytest.fixture
def moscow():
    """now для заданного дня и часа по Москве: moscow(2026, 10, 21, 14)."""
    import datetime as dt
    from office import config

    def f(y, m, d, h, mi=0):
        return dt.datetime(y, m, d, h, mi, tzinfo=config.TZ).timestamp()
    return f


class FakeResp:
    def __init__(self, text="", status=200, js=None):
        self.text, self.status_code, self._js = text, status, js

    def json(self):
        return self._js if self._js is not None else json.loads(self.text)


class FakeSession:
    """requests-подобный объект: url-подстрока → ответ."""
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, **kw):
        self.calls.append(url)
        for k, v in self.routes.items():
            if k in url:
                return v if isinstance(v, FakeResp) else FakeResp(v)
        return FakeResp("", 404)

    post = put = get
