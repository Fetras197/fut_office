"""Конфигурация: пути, часовой пояс, пределы. Секреты — только из окружения (Secrets GitHub) или .env."""
import os
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = Path(os.environ.get("FO_STATE", ROOT / "state"))
DB_PATH = Path(os.environ.get("FO_DB", STATE_DIR / "office.db"))
TZ = ZoneInfo("Europe/Moscow")        # запретные часы заданы по Москве; владелец в Минске (то же UTC+3)
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
REPO = os.environ.get("GITHUB_REPOSITORY", "Fetras197/fut_office")

TAX = 0.05
MIN_PROFIT = 500
MIN_ROI = 0.07
MIN_SCORE = 60
MAX_SIGNALS_PER_DAY = 10
MIN_DEPTS = 2
DEDUP_HOURS = 6
PHONE_STALE_MIN = 90          # данные телефона старше — отдел «рынок» молчит


def load_env(path: Path = ROOT / ".env") -> None:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        pass

IGNORE_HOURS = os.environ.get("FO_IGNORE_HOURS") == "1"      # только для живой проверки днём/ночью: отключает запретные часы (в боевом режиме не ставить)
