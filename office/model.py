"""Общие структуры: мнение отдела о карте."""
from dataclasses import dataclass, field


@dataclass
class Opinion:
    dept: str                 # intel | market | sbc | football
    ea_id: int
    name: str
    version: str              # русское название версии карты
    rating: int
    strength: float           # 0..1
    kind: str                 # flip | invest | fodder
    reason: str               # одна строка по-русски (без латиницы, кроме имени игрока)
    srcs: list = field(default_factory=list)   # источники (названия)
    buy_max: int | None = None
    sell_ref: int | None = None      # ориентир цены продажи (до налога)
    ttl_h: float = 6.0
    horizon_d: float = 0.1
    veto: str | None = None
    liq: int | None = None       # продаж за сутки, если известно
