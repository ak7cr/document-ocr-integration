# Document OCR integration

Full-stack invoice OCR: EasyOCR spatial cell-grid table engine with PostgreSQL template auto-learning served behind a **React/Vite frontend**. Local OCR runs first (their engine); when its output is weak, the pipeline falls back to **Claude** (primary) then **Gemini** (backup).

## Stack

- **Backend**: `backend/` — FastAPI + SQLAlchemy + their `services/*` (EasyOCR full-page OCR, 2D cell-grid table engine, geometry template engine, validator). Claude wired in as the primary AI fallback.
- **Frontend**: `client/` — React + Vite, proxies `/api` → `:8000`.
- **DB**: Postgres — their schema (`invoice_templates`, `extraction_logs`, `extraction_reviews`), auto-created on startup. SQLite `templates.db` used only if Postgres is unavailable.
- **Compatibility layer**: `backend/adapter.py` exposes our frontend's contract (`/api/extractions`, `/api/templates`, `/api/extractions/:id/corrections`, `/api/health`) on top of their native `/api/extract-invoice` pipeline.

## Repository layout

- `backend/` — FastAPI app + services (`adapter.py`, `main.py`, `services/*`) — the API served on :8000 (uvicorn).
- `client/` — React/Vite frontend (dev server :5173, proxies `/api` → `:8000`).
- `testing/` — sample invoices / PDFs used for manual checks.
- `requirements.txt` / `package.json` — Python and Node dependencies.

## Run locally

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
npm install
cp .env.example .env        # set DATABASE_URL, ANTHROPIC_API_KEY, GEMINI_API_KEY
npm run dev                 # concurrently starts uvicorn (:8000) + vite (:5173)
```

Open http://localhost:5173. The backend auto-creates its tables in Postgres on startup (`db:init` script also does this).

## Extraction flow

1. Upload a PDF/image; choose **Automatic**, **AI-assisted**, **Local OCR only**, or **PDFPlumber**.
2. Their engine OCRs the full page (EasyOCR) and matches a learned geometry template (`invoice_templates`) — fast path if validation ≥ 0.98.
3. Otherwise the 2D cell-grid engine extracts line items; weak/flagged output routes to **Claude vision** (primary), then **Gemini** (backup).
4. Result is validated, logged to `extraction_logs`, and the layout auto-learns into `invoice_templates`.
5. Review in the UI; **Save as template** / **Submit Corrections** write to `invoice_templates` / `extraction_reviews`.

`DATABASE_URL` is required for templates/history. Configure `ANTHROPIC_API_KEY` (primary) and `GEMINI_API_KEY` (fallback). Without keys, local extraction still works.

