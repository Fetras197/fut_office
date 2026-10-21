# PROGRESS — FUT-офис

- 2026-10-21 Старт. План (PLAN.md) проверен gemini-plan, решения в DECISIONS.md. Разведка сети: t.me/s (5 каналов) 200, YouTube RSS 200, fut.gg и r2 200, futbin.com 200 (с телефона), ESPN и FPL 200, Reddit 403, Sportskeeda 403.
- 02:00–02:30 Этапы 1–5 написаны: office/{config,db,ru,http,llm,telegram,cards,intel,sbc,football,market,risk,trader,accountant,link,cloud}.py, sources.yaml, workflow office.yml. Репо Fetras197/fut_office создан, Secrets TG_TOKEN/TG_CHAT/GEMINI_API_KEY заведены (OPENROUTER_API_KEY локально не найден). Три прогона в Actions успешны; setup-python убран (проход ≈1–1,5 мин после первого).
- Телефон: fetras/fut_bot/bot/office_link.py (+ подключение в main.py, тесты test_office_link.py). 6 старых тестов scan_replica/tier2 падают из-за чужого WIP сканера, не из-за офиса.
- Добавлено: отдел «возврат к медиане» (sbc.reversion_opinions), промо-прогнозы, пауза LLM 20 мин, видео — в конце прохода.
- ~02:50 Android убил процессы (signal 9, нехватка памяти): живая проверка (2 ч) и relay-review прерваны. Правило дальше: один тяжёлый процесс, тесты по файлу.
- Осталось: прогнать тест русского языка отделов, этап 6 (живая проверка заново), relay-review, отчёты в ~/dev/reports, gitsave.
