# FUT-офис (EA FC 27, консоль)

Подсказки по торговле картами в Telegram. Только подсказки: ни автобайера, ни входа в аккаунт EA, ни покупки монет.

Отделы: разведка (`office/intel.py`, источники в `sources.yaml`), аналитик рынка (телефон, бот `fetras/fut_bot` + читатель `office/market.py`),
аналитик наборов и прогноза (`sbc.py`), футбольный аналитик (`football.py`), риск-менеджер (`risk.py`), трейдер (`trader.py`), бухгалтер и совет (`accountant.py`).

- Облако: GitHub Actions (`.github/workflows/office.yml`), состояние — ветка `state`.
- Телефон → облако: ветка `phone` (`phone_feed.json`, `ledger.json`). Облако → телефон: `state/signals.json`.
- Запуск вручную: `python -m office cloud` (FO_DRY=1 — сообщения в консоль). Отчёт: `python -m office report`. Тесты: `python -m pytest -q`.
- Ключи (Secrets): TG_TOKEN, TG_CHAT, GEMINI_API_KEY, OPENROUTER_API_KEY (+ необязательно GROQ/CEREBRAS/MISTRAL).
