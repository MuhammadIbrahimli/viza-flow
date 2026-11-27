# VizaFlow Backend

VizaFlow is a Python backend for preparing visa application documents. It accepts scanned source documents, extracts applicant information with Gemini, and generates completed PDF forms, a cover letter, and a document checklist. Italy/Schengen is the default target; the CLI also supports a USA form target.

Names are transliterated according to ICAO Doc 9303 and dates use `DD/MM/YYYY`. API uploads are processed in memory and temporary working files are removed after the request.

## Requirements

- Python 3.10+
- A Gemini API key
- `cloudflared` if you want to expose a local API through Cloudflare Tunnel
- Poppler for processing PDF scans with `pdf2image`

## Install

```bash
git clone https://github.com/MuhammadIbrahimli/viza-flow.git
cd viza-flow
python -m venv .venv
```

Activate the environment and install requirements:

```bash
# Windows
.venv\Scripts\activate

# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

Copy `.env.example` to `.env` and configure at least:

```dotenv
GEMINI_API_KEY=your_gemini_api_key
HOST=127.0.0.1
PORT=8000
CORS_ORIGINS=https://your-frontend.example
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

Set only the integrations you use. Keep secrets out of version control. Restrict `CORS_ORIGINS` to the frontend origins you trust.

## Run the API

```bash
python run_server.py
```

The API listens on `127.0.0.1:8000` by default. Check it with `GET /ping`.

To run the API and a temporary Cloudflare quick tunnel together:

```bash
# Windows
start_all.bat

# Linux/macOS
chmod +x start_all.sh
./start_all.sh
```

A quick tunnel URL changes when the tunnel restarts. Update the frontend's `NEXT_PUBLIC_API_BASE_URL` and rebuild or redeploy it after the URL changes. For a stable public endpoint, configure a named tunnel using the template in `cloudflared/`.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/ping` | Process status and uptime |
| GET | `/api/health` | Health check |
| GET | `/api/defaults` | Default trip and hotel values |
| POST | `/api/process` | Process uploaded documents and return JSON or ZIP output |
| POST | `/api/pack` | Package generated forms, letter, and checklist |
| POST | `/api/demo` | Generate a sample package without uploaded scans |

The process request accepts files plus trip dates, hotel, notes, and a response format. Check the API implementation before integrating against field details.

## Command-line scripts

```bash
python scripts/fill_form.py
python scripts/fill_form.py --arrival 10/09/2026 --departure 17/09/2026 --hotel "Hotel Artemide, Rome"
python scripts/create_schengen_template.py
```

The default form is for Italy/Schengen. Use `--target-country usa` for the USA target.

## Repository boundary

This repository contains the API and document-processing pipeline. The browser interface lives in the separate [VizaFlow Frontend](https://github.com/MuhammadIbrahimli/viza-flow-frontend) repository.

## Troubleshooting

- Missing model credentials: configure `GEMINI_API_KEY` in `.env`.
- Model not found: the code tries compatible Flash models; set `GEMINI_MODEL` if needed.
- `/ping` is unavailable: confirm that the API process is running on the configured host and port.

## License

See [LICENSE](LICENSE).