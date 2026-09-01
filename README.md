# Visa Generator · VizaFlow Backend

Автозаполнение визовых документов для агентств в Баку: сканы → Gemini → анкета PDF, сопроводительное письмо PDF и чек-лист TXT.

Имена транслитерируются по ICAO Doc 9303, даты — `DD/MM/YYYY`. Сканы через API не пишутся в `input/` / `output/`: только RAM и `temp/` (каталог процесса удаляется после ответа).

## Сценарий 1 — Local Host + Cloudflare Tunnel

Бекенд слушает только `127.0.0.1:8000`. Туннель Cloudflare публикует локальный API в интернет без открытия портов на роутере.

### Что нужно

- Python 3.10+
- Ключ Gemini: [Google AI Studio](https://aistudio.google.com/apikey)
- [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) в PATH
- Для PDF-сканов — [Poppler](https://github.com/osber/poppler-windows/releases) (`pdf2image`)

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Скопируйте `.env.example` в `.env` и укажите ключ:

```
GEMINI_API_KEY=ваш_ключ
HOST=127.0.0.1
PORT=8000
CORS_ORIGINS=*
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
```

### Запуск

Одним скриптом (API + quick tunnel):

```bat
start_all.bat
```

Linux/macOS: `chmod +x start_all.sh && ./start_all.sh`

В логе cloudflared появится URL вида `https://….trycloudflare.com`. Проверка:

```
GET /ping
```

Этот же адрес укажите во фронтенде (`visaflow-frontend` на Vercel):

```
NEXT_PUBLIC_API_BASE_URL=https://случайный-адрес.trycloudflare.com
```

Quick tunnel каждый раз выдаёт **новый** URL — после перезапуска cloudflared обновите переменную и сделайте Redeploy.

Только API без туннеля:

```bash
python run_server.py
```

Именованный туннель (свой домен): скопируйте `cloudflared/config.yml.template` → `cloudflared/config.yml` и подставьте Tunnel ID / hostname. Ingress уже указывает на `http://127.0.0.1:8000`.

### API

| Метод | Путь | Ответ |
|---|---|---|
| GET | `/ping` | статус и uptime |
| GET | `/api/health` | `{ "status": "ok" }` |
| GET | `/api/defaults` | даты/отель по умолчанию |
| POST | `/api/process` | JSON (base64: 2 PDF + 1 TXT). Form: `files`, `arrival_date`, `departure_date`, `hotel`, `notes`, `response_format=json\|zip` |
| POST | `/api/pack` | ZIP с `filled_*_schengen.pdf`, `cover_letter_*.pdf`, `document_checklist_*.txt` |
| POST | `/api/demo` | пакет-образец (без сканов) |

Тяжёлая генерация PDF идёт в thread pool и не блокирует event loop. CORS разрешён (см. `CORS_ORIGINS`).

## Структура бекенда

```
app/
  main.py              # FastAPI
  config.py            # .env, пути, HOST/PORT
  services/
    gemini.py          # извлечение полей через Gemini
    documents.py       # PDF/TXT и ZIP
    pipeline.py        # ICAO, заполнение бланка
    schengen_template.py
    overlay.py
temp/                  # временные артефакты (не коммитить)
templates/             # бланк Шенгена и схема DS-160
run_server.py          # uvicorn 127.0.0.1:8000
```

UI живёт в отдельном проекте `visaflow-frontend` (Vercel). Этот репозиторий — только API.

## CLI (папка input/ → output/)

```bash
python scripts/fill_form.py
python scripts/fill_form.py --arrival 10/09/2026 --departure 17/09/2026 --hotel "Hotel Artemide, Rome"
python scripts/create_schengen_template.py
```

Италия / Шенген по умолчанию. США: `--target-country usa`.

## Если ошибка

- Нет ключа — задайте `GEMINI_API_KEY` в `.env`.
- 404 по модели — скрипт переберёт бесплатные Flash; при необходимости укажите `GEMINI_MODEL`.
- `/ping` не отвечает — API не запущен или слушает другой порт.
