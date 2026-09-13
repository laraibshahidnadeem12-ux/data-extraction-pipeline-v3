# OCR Data Extraction Pipeline

Upload an **invoice** and/or a **CNIC** image. The pipeline runs OCR, extracts the
person / customer **name** and the relevant **date**, appends the result to an
**Excel** workbook (and optionally a **Google Sheet**), and returns the structured
record as **JSON or CSV** — either from the web UI or the REST API.

## Architecture

```
templates/index.html   drag & drop upload interface (Flask-rendered)
app.py                 Flask app: /api/extract, /api/records, downloads
pipeline/ocr.py        OCR engines (Gemini structured extraction / Google Vision fallback)
pipeline/extractor.py  heuristic parser + date normalization (fallback path)
pipeline/storage.py    Excel (openpyxl), Google Sheets (gspread), CSV, JSON store
config.json            keys, engine, Google Sheet + output settings
outputs/               extracted_data.xlsx, records.json (created at runtime)
data/uploads/          copies of uploaded files
```

Pipeline flow: **upload → save file → OCR → extract name/date → append to
Excel → (optional) append to Google Sheets → return JSON/CSV.**

## Setup

```bash
pip install -r requirements.txt
```

Python 3.14 verified. Requires an internet connection for the OCR APIs.

### 1. Configure OCR credentials — `config.json`

Set at least one key (both is best; Gemini is used when present, Google Vision
is the fallback):

```jsonc
"ocr": {
  "engine": "gemini",              // "gemini" or "vision"
  "gemini_api_key": "AIza...",     // https://aistudio.google.com/apikey
  "gemini_model": "gemini-2.0-flash",
  "google_vision_api_key": "AIza..."   // https://console.cloud.google.com > Vision API
}
```

- **Gemini** (recommended): returns a structured JSON object directly — best
  accuracy for names/dates.
- **Google Vision**: `DOCUMENT_TEXT_DETECTION` — raw text passed through the
  heuristic extractor.

### 2. (Optional) Google Sheets sync

1. Create a Google **service account** (IAM & Admin → Service Accounts) and
   download its JSON key as `service_account.json` in the project root.
2. Create a spreadsheet and share it **with the service account email** (Edit rights).
3. Enable sync in `config.json`:

```jsonc
"google_sheets": {
  "enabled": true,
  "service_account_json": "service_account.json",
  "spreadsheet_id": "1AbCdEf... (from the sheet URL)",
  "sheet_tab": "ExtractedData"
}
```

If sync is disabled or fails (e.g. no credential file), extraction still succeeds
and data is preserved in Excel/JSON.

## Run

```bash
python app.py          # then open http://127.0.0.1:5000
```

Not on PATH? The global Python may shuffle scripts elsewhere, but `python app.py`
always works from the project folder.

## API

| Endpoint | Description |
| --- | --- |
| `GET /` | Upload interface |
| `POST /api/extract` | Multipart upload, fields `invoice` and/or `cnic` (multiple files each). Returns JSON collection: `{success, message, records[{name, date, date_normalized, document_type, fields, confidence, engine}], excel_path, sheets_synced}` |
| `POST /api/extract?format=csv` | Same, but returns the record(s) as `.csv` |
| `GET /api/records` | All records (JSON or `?format=csv`) |
| `GET /download/csv` | All records as CSV |
| `GET /download/excel` | The `.xlsx` workbook |
| `GET /health` | Status + active engine |

### Example

```bash
curl -F "invoice=@invoice.jpg" -F "cnic=@cnic.png" http://127.0.0.1:5000/api/extract
curl -F "invoice=@invoice.jpg" -F "format=csv" http://127.0.0.1:5000/api/extract
```

## Accepted uploads

Images: PNG, JPG, JPEG, WEBP, BMP, TIFF, GIF. PDFs are supported too —
the pipeline rasterizes each page (PyMuPDF) before OCR. Max upload 20 MB.

## Output columns

`timestamp, source_file, document_type, name, date, date_normalized, fields,
engine, confidence`

`date_normalized` is the date coerced to ISO `YYYY-MM-DD`; `fields` holds extra
recognized values (invoice number, total amount, CNIC number, gender, etc.) as a
JSON string in Excel/CSV and as key/value pairs in the JSON response.