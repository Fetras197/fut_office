"""Русский язык: склонения, форматы денег и времени, проверка отсутствия латиницы в текстах для Telegram."""
import re
from datetime import datetime

from . import config


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if 11 <= n % 100 <= 14:
        return many
    return one if n % 10 == 1 else few if 2 <= n % 10 <= 4 else many


def coins(n: float) -> str:
    n = int(round(n))
    return f"{n:,}".replace(",", " ") + " " + plural(n, "монета", "монеты", "монет")


def num(n: float) -> str:
    return f"{int(round(n)):,}".replace(",", " ")


def hhmm(ts: float) -> str:
    return datetime.fromtimestamp(ts, config.TZ).strftime("%H:%M")


def ago(sec: float) -> str:
    m = int(sec // 60)
    if m < 1:
        return "только что"
    if m < 60:
        return f"{m} {plural(m, 'минуту', 'минуты', 'минут')} назад"
    h = m // 60
    if h < 48:
        return f"{h} {plural(h, 'час', 'часа', 'часов')} назад"
    d = h // 24
    return f"{d} {plural(d, 'день', 'дня', 'дней')} назад"


LATIN = re.compile(r"[A-Za-z]")


def no_latin(text: str, allow: tuple[str, ...] = ()) -> bool:
    """В тексте нет латиницы (имена игроков из allow вырезаются перед проверкой)."""
    for a in allow:
        if a:
            text = text.replace(a, "")
    return not LATIN.search(text)


# названия для сообщений: английские коды версий карт → русские
VERSIONS = {
    "totw": "Команда недели", "toty": "Команда года", "tots": "Команда сезона", "if": "Ин-форм", "inform": "Ин-форм",
    "gold": "Золотая", "rare": "Золотая редкая", "icon": "Икона", "hero": "Герой", "evo": "Эволюция", "fs": "Звёзды будущего",
    "fut_birthday": "День рождения", "halloween": "Хеллоуин", "scream": "Крик", "rttk": "Путь к финалу", "potm": "Игрок месяца",
    "sbc": "Карта из заданий", "objective": "Карта из заданий", "other": "Особая",
}
SIGNAL_TYPES = {"fodder": "корм под наборы обмена (СБЧ)", "cycle": "недельный цикл", "totw": "кандидат в команду недели или ин-форм", "channel": "сигнал канала, проверенный по ценам"}
SOURCE_KINDS = {"telegram": "Телеграм", "youtube": "Ютуб", "site": "сайт", "reddit": "Реддит"}
DEPTS = {"intel": "разведка", "market": "аналитик рынка", "sbc": "аналитик наборов и прогноза", "football": "футбольный аналитик"}
VOTERS = ("intel", "sbc", "football")      # отделы, которые голосуют «за»; аналитик рынка проверяет цену и тренд, риск-менеджер накладывает запрет
