# Valor — платформа администрирования фондов (демо)

По ТЗ «Valor Fund Administration Platform, Technical Specification v0.3» (04.11.2026).
Сейчас реализовано расчётное ядро (Phase 1 по ТЗ) и демо-кабинет. Адрес: https://centrum.kz/valor-test/

## Из чего состоит

| Папка | Что внутри |
|---|---|
| `engine/` | Расчётное ядро. Чистый Python и Decimal, без базы, времени и случайности (ARC-01). Модули: распределение по ступеням `waterfall.py`, 8% через IRR `irr.py`, комиссия за управление и зачёт `fees.py`, валюта `fx.py`, выравнивание `equalisation.py`, счета Valor по FAA `admin_fees.py`, сроки FAA и ошибки NAV `sla.py`. Условия фонда задаются настройками в `config.py`, а не кодом |
| `funds/` | Django: модели (реестр, взносы, сделки, распределения, проводки, журнал событий), страницы кабинета EN/RU, правило «четырёх глаз» |
| `funds/golden.py` | Контрольные примеры из Appendix A ТЗ, пересчитываются вживую на странице «Приёмочные тесты» |
| `tests/` | pytest: контрольные примеры, ядро, контроли платформы |
| `deploy/` | Выкладка на сервер, бэкап |

## Запуск у себя

```bash
docker compose up -d db          # своя база Valor на порту 5433
cp deploy/env.prod.example .env  # для локальной работы: DJANGO_DEBUG=1, POSTGRES_PORT=5433, DEMO_PASSWORD=...
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_demo
.venv/bin/python manage.py runserver 127.0.0.1:8100
.venv/bin/pytest -q
```

Демо-пользователи: `operator` (создаёт), `checker` (утверждает), `gp`, `auditor`. Пароль берётся из `DEMO_PASSWORD`.

## Сервер

Valor полностью отделён от лизинга: своя папка `/opt/valor`, свой docker-compose проект `valor`, своя база `valor-db` (том `valor_valor_pgdata`) и своя внутренняя сеть.
С остальным сервером Valor связан только внешней сетью `edge`: через неё общий Caddy отправляет запросы `centrum.kz/valor-test/*` в контейнер `valor-web`.

- Выкладка с Mac: `./deploy/release.sh` (тесты → копия кода → `deploy/deploy.sh` на сервере)
- Бэкап: `deploy/backup.sh` (cron, см. `deploy/crontab.example`) → `/opt/backups/valor`

### Переезд на другой сервер

1. На старом сервере: `deploy/backup.sh`, скопировать дамп и `/opt/valor/.env`.
2. На новом сервере: `VALOR_HOST=user@new ./deploy/release.sh`, положить `.env`, восстановить дамп через `pg_restore` в контейнер `valor-db`.
3. В прокси (Caddy) указать новый адрес или отдельный домен Valor.

## Что дальше по ТЗ

Phase 2: GL и интеграция с 1С (NAV-02), перенос данных из таблиц (MIG-01/02).
Phase 3: портал LP, AI-функции (AI-01..06), AML-скрининг, хранилище документов.
Phase 4: SSO, шифрование, DR и хранение данных в МФЦА (NFR-03).
