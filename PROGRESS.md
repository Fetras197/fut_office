# PROGRESS — FUT-офис

- 2026-10-21 Старт. План (PLAN.md) проверен gemini-plan, решения в DECISIONS.md. Разведка сети: t.me/s (5 каналов) 200, YouTube RSS 200, fut.gg и r2 200, futbin.com 200 (с телефона), ESPN и FPL 200, Reddit 403, Sportskeeda 403.
- 02:00–02:30 Этапы 1–5 написаны: office/{config,db,ru,http,llm,telegram,cards,intel,sbc,football,market,risk,trader,accountant,link,cloud}.py, sources.yaml, workflow office.yml. Репо Fetras197/fut_office создан, Secrets TG_TOKEN/TG_CHAT/GEMINI_API_KEY заведены (OPENROUTER_API_KEY локально не найден). Три прогона в Actions успешны; setup-python убран (проход ≈1–1,5 мин после первого).
- Телефон: fetras/fut_bot/bot/office_link.py (+ подключение в main.py, тесты test_office_link.py). 6 старых тестов scan_replica/tier2 падают из-за чужого WIP сканера, не из-за офиса.
- Добавлено: отдел «возврат к медиане» (sbc.reversion_opinions), промо-прогнозы, пауза LLM 20 мин, видео — в конце прохода.
- ~02:50 Android убил процессы (signal 9, нехватка памяти): живая проверка (2 ч) и relay-review прерваны. Правило дальше: один тяжёлый процесс, тесты по файлу.
- Осталось: прогнать тест русского языка отделов, этап 6 (живая проверка заново), relay-review, отчёты в ~/dev/reports, gitsave.
- тесты по файлам зелёные, gitsave выполнен
- 06:05 живая проверка 2 ч перезапущена (лог /tmp/live_check.log, итог reports/live_check.md), конец ≈ +2 ч
- отчёт этапов 1–5 запушен в dev-reports
- 08:10 Этап 6, первая попытка (06:05–08:05): 6 проходов, 0 сигналов. Причина: телефонный бот остановлен Android'ом в 02:35 (последняя цена сканера в базе старше 5 ч), отдел «рынок» молчал (hang_rows=0); без него согласия двух отделов не набралось (разведка 2 мнения, футбол 6, пересечений нет). Критерий 70% не проверен (0 из 0) — НЕ засчитан. Нужно: владелец делает в Termux `futbot restart`, затем `python3 tools/live_check.py --minutes 120` повторно.
- 08:40 OPENROUTER_API_KEY заведён в Secrets (потолок 0,20 $/сутки на оба офиса — llm.OR_DAILY_CAP_USD). Минуты Actions за дни 18–21.10 (по длительности задач, округление вверх): trading_office 31/40/40 мин в день (10–15 запусков в день — GitHub пропускает часть cron), fut_office ≈45–60 мин/день при плане 30 запусков → прогноз на месяц ≈1200 + ≈1500 = больше 2000. История обоих репозиториев (все ветки, все блобы, в т.ч. state/office.db) проверена по шаблонам sk-, sk-or-, gsk_, AKIA, ghp_, github_pat_, AIza, csk-, токены Telegram, ключи Alpaca PK…: ключей нет. Оба репозитория сделаны публичными. В trading_office в истории есть числовой id чата владельца (не секрет) и журнал бумажной торговли.
- Хаб FUT Reveal удалён из sources.yaml; пересказ видео — не чаще раза в 3 часа (intel.VIDEO_EVERY), тест добавлен.
