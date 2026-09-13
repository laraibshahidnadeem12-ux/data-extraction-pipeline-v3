"""OCR & Data Extraction Pipeline -- Flask app.

Upload an invoice and/or a CNIC image. The pipeline runs OCR, extracts the
person/customer name and date, appends the result to an Excel workbook (and
optionally a Google Sheet), and returns the structured record as JSON or CSV.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from flask import (
    Flask,
    Response,
    jsonify,
    render_template,
    request,
    send_file,
)
from werkzeug.utils import secure_filename

from pipeline.extractor import find_dates
from pipeline.models import ExtractedRecord
from pipeline.ocr import extract_document, sniff_extension
from pipeline.storage import (
    append_to_excel,
    load_records,
    records_to_csv,
    save_records,
    sync_to_sheets,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
    CONFIG = json.load(fh)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB

ALLOWED_EXT = {"png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff", "gif", "pdf"}
FIELD_HINTS = {"invoice": "invoice", "cnic": "cnic"}
EXCEL_PATH = CONFIG.get("output", {}).get("excel_path", "outputs/extracted_data.xlsx")
RECORDS_PATH = CONFIG.get("output", {}).get("records_json", "outputs/records.json")
UPLOAD_DIR = CONFIG.get("output", {}).get("upload_dir", "data/uploads")


def _run_extraction(files: dict) -> list[dict]:
    records = []
    for field, hint in FIELD_HINTS.items():
        for upload in files.getlist(field):
            content = upload.read()
            original = upload.filename or ""
            orig_ext = original.rsplit(".", 1)[-1].lower() if "." in original else ""
            ext = sniff_extension(content) or orig_ext or ""
            if ext not in ALLOWED_EXT:
                raise ValueError(
                    f"File '{original or '<unnamed>'}' has an unsupported type."
                )
            base = original.rsplit(".", 1)[0] if "." in original else original
            base = secure_filename(base) or "upload"
            filename = f"{base}.{ext}"
            os.makedirs(UPLOAD_DIR, exist_ok=True)
            with open(os.path.join(UPLOAD_DIR, filename), "wb") as fh:
                fh.write(content)

            result = extract_document(content, filename, CONFIG, hint)

            date_raw = result.get("date") or ""
            date_norm = result.get("date_normalized") or ""
            if not date_norm and date_raw:
                parsed = find_dates(date_raw)
                date_norm = parsed[0] if parsed else ""
            if not date_norm:
                parsed = find_dates(result.get("raw_text") or "")
                if parsed:
                    date_norm = parsed[0]

            records.append(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "source_file": filename,
                    "document_type": result.get("document_type", hint),
                    "name": result.get("name") or "",
                    "date": date_raw,
                    "date_normalized": date_norm,
                    "fields": result.get("fields") or {},
                    "engine": result.get("engine")
                    or CONFIG.get("ocr", {}).get("engine", "gemini"),
                    "confidence": result.get("confidence"),
                }
            )
    if not records:
        raise ValueError("No files were uploaded.")
    return records


def _persist(records: list[dict]) -> dict:
    all_records = load_records(RECORDS_PATH)
    all_records.extend(records)
    save_records(all_records, RECORDS_PATH)

    rows_models = [
        ExtractedRecord(
            source_file=r["source_file"],
            document_type=r["document_type"],
            name=r["name"],
            date=r["date"],
            date_normalized=r["date_normalized"],
            fields=r["fields"],
            engine=r["engine"],
            confidence=r.get("confidence"),
        )
        for r in records
    ]
    excel_path = append_to_excel(rows_models, EXCEL_PATH)
    sheets_synced = sync_to_sheets(records, CONFIG)
    return {"excel_path": os.path.abspath(excel_path), "sheets_synced": sheets_synced}


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/extract")
def api_extract():
    """Upload + extract. Query param ?format=csv returns CSV instead of JSON."""
    fmt = request.args.get("format", "json").lower()
    try:
        records = _run_extraction(request.files)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001 - surface any pipeline error
        return jsonify({"success": False, "error": str(exc)}), 500

    try:
        persistence = _persist(records)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"success": False, "error": f"Storage failed: {exc}"}), 500

    payload = {
        "success": True,
        "message": f"Extracted and stored {len(records)} record(s).",
        "records": records,
        "excel_path": persistence["excel_path"],
        "sheets_synced": persistence["sheets_synced"],
    }
    if fmt == "csv":
        csv_data = records_to_csv(records)
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=extracted.csv"},
        )
    return jsonify(payload)


@app.get("/api/records")
def api_records():
    fmt = request.args.get("format", "json").lower()
    records = load_records(RECORDS_PATH)
    if fmt == "csv":
        return Response(
            records_to_csv(records),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=records.csv"},
        )
    return jsonify({"records": records, "count": len(records)})


@app.get("/download/csv")
def download_csv():
    records = load_records(RECORDS_PATH)
    return Response(
        records_to_csv(records),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=extracted_data.csv"},
    )


@app.get("/download/excel")
def download_excel():
    if not os.path.exists(EXCEL_PATH):
        return jsonify({"success": False, "error": "No data extracted yet."}), 404
    return send_file(os.path.abspath(EXCEL_PATH), as_attachment=True,
                     download_name="extracted_data.xlsx")


@app.get("/health")
def health():
    return jsonify({"status": "ok", "engine": CONFIG.get("ocr", {}).get("engine")})


if __name__ == "__main__":
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    app.run(host="127.0.0.1", port=5000, debug=True, use_reloader=False)