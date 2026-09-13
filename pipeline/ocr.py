"""OCR layer.

Two engines are supported, both via plain REST calls (no heavyweight SDKs):

* Gemini  -- multimodal vision model that returns a structured JSON object
             directly (default engine, gives the most accurate extraction).
* Google Vision -- DOCUMENT_TEXT_DETECTION; returns raw text plus word-level
             bounding boxes used by the heuristic extractor as a fallback.
"""
from __future__ import annotations

import base64
import io
import json
import mimetypes
import re

import requests

from .extractor import extract_with_heuristics

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent?key={api_key}"
)
VISION_URL = (
    "https://vision.googleapis.com/v1/images:annotate?key={api_key}"
)

IMAGE_MIMES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "bmp": "image/bmp",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    "gif": "image/gif",
}


def guess_mime(filename: str) -> str:
    base, ext = mimetypes.guess_type(filename)
    if base:
        return base
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return IMAGE_MIMES.get(ext, "application/octet-stream")


def is_pdf(filename: str, mime: str) -> bool:
    return mime == "application/pdf" or filename.lower().endswith(".pdf")


def sniff_extension(content: bytes) -> str | None:
    """Detect the real file type from its magic bytes (independent of filename)."""
    if content.startswith(b"%PDF"):
        return "pdf"
    if content.startswith(b"\x89PNG"):
        return "png"
    if content.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if content.startswith(b"BM"):
        return "bmp"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "webp"
    if content.startswith((b"II*\x00", b"MM\x00*")):
        return "tiff"
    return None


def compress_image(content: bytes, mime: str, max_dim: int = 2000) -> tuple[bytes, str]:
    """Downscale + re-encode oversized raster images so the OCR request is fast.

    Returns (bytes, mime). Non-raster content (e.g. PDF frames already sent as
    PNG) or images under the size threshold are returned unchanged.
    """
    if max_dim <= 0:
        return content, mime
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(content))
        img.load()
    except Exception:
        return content, mime

    if max(img.size) <= max_dim:
        return content, mime

    if img.mode != "RGB":
        try:
            img = img.convert("RGB")
        except Exception:
            return content, mime

    ratio = max_dim / float(max(img.size))
    new_size = (max(1, int(img.size[0] * ratio)), max(1, int(img.size[1] * ratio)))
    try:
        img = img.resize(new_size, Image.LANCZOS)
    except Exception:
        return content, mime

    buf = io.BytesIO()
    try:
        img.save(buf, format="JPEG", quality=85, optimize=True)
    except Exception:
        return content, mime
    compressed = buf.getvalue()
    if len(compressed) >= len(content):
        return content, mime  # JPEG didn't actually help — keep the original
    return compressed, "image/jpeg"


def prepare_image(content: bytes, filename: str):
    """Return (image_bytes, mime) ready for the OCR APIs.

    PDFs are rasterized to a PNG at a reasonable DPI so they can be sent to
    the synchronous OCR endpoints.
    """
    mime = guess_mime(filename)
    if is_pdf(filename, mime):
        try:
            import fitz  # PyMuPDF
        except ImportError:
            raise RuntimeError(
                "PyMuPDF is required to handle PDF uploads. "
                "Install it with: pip install PyMuPDF"
            )
        pages = []
        with fitz.open(stream=content, filetype="pdf") as doc:
            for page_no in range(min(len(doc), 10)):
                pix = doc[page_no].get_pixmap(dpi=200)
                pages.append((pix.tobytes("png"), "image/png"))
        if not pages:
            raise RuntimeError("PDF contains no page to OCR.")
        if len(pages) == 1:
            return pages[0]
        return pages  # multiple pages -> list of frames
    return content, mime


def _json_body(content, mime) -> dict:
    return {
        "image": {
            "content": base64.b64encode(content).decode("ascii"),
        },
        "mime": mime,
    }


def vision_annotate(content: bytes, mime: str, api_key: str, timeout: int = 60) -> dict:
    """Run DOCUMENT_TEXT_DETECTION and return text, words, and paragraphs."""
    payload = {
        "requests": [
            {
                "image": {"content": base64.b64encode(content).decode("ascii")},
                "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
            }
        ]
    }
    resp = requests.post(
        VISION_URL.format(api_key=api_key), json=payload, timeout=timeout
    )
    resp.raise_for_status()
    data = resp.json()
    annotations = (data.get("responses") or [{}])[0]
    if "error" in annotations:
        raise RuntimeError(f"Vision API error: {annotations['error']}")

    full_annotation = annotations.get("fullTextAnnotation") or {}
    text = full_annotation.get("text", "")

    words = []
    for ann in annotations.get("textAnnotations", [])[1:]:
        vertices = ann.get("boundingPoly", {}).get("vertices", [])
        if len(vertices) < 4:
            continue
        x0 = min(v.get("x", 0) for v in vertices)
        y0 = min(v.get("y", 0) for v in vertices)
        x1 = max(v.get("x", 0) for v in vertices)
        y1 = max(v.get("y", 0) for v in vertices)
        words.append(
            {"text": ann.get("description", ""), "x0": x0, "y0": y0,
             "x1": x1, "y1": y1}
        )

    paragraphs = []
    for page in full_annotation.get("pages", []):
        for block in page.get("blocks", []):
            for para in block.get("paragraphs", []):
                para_text = "".join(
                    word.get("text", "")
                    for word in para.get("words", [])
                )
                box = para.get("boundingBox", {}).get("vertices", [])
                if not para_text:
                    continue
                x0 = min(v.get("x", 0) for v in box)
                y0 = min(v.get("y", 0) for v in box)
                paragraphs.append(
                    {"text": para_text, "x0": x0, "y0": y0,
                     "x1": max((v.get("x", 0) for v in box), default=0),
                     "y1": max((v.get("y", 0) for v in box), default=0)}
                )
    paragraphs.sort(key=lambda p: (p["y0"], p["x0"]))
    return {"text": text, "words": words, "paragraphs": paragraphs}


GEMINI_PROMPT = """You are a document data extraction engine.
Extract structured data from the attached document image.

Return ONLY a valid JSON object (no markdown, no prose) with this schema:
{
  \"document_type\": \"invoice\" | \"cnic\" | \"other\",
  \"name\": \"person or customer name found in the document as a string\",
  \"date\": \"the most relevant date, as ISO YYYY-MM-DD when derivable otherwise as written\",
  \"confidence\": <number 0.0 to 1.0>,
  \"fields\": { <any other recognized field names as keys, string/number values> }
}

Rules:
- For an invoice, \"name\" is the customer / bill-to name and \"date\" is the
  invoice or issue date. Useful extra fields: invoice_number, vendor_name,
  due_date, total_amount, currency.
- For a CNIC, \"name\" is the card holder's name and \"date\" is the date of
  birth. Useful extra fields: identity_number, father_name, date_of_birth,
  gender, date_of_issue, date_of_expiry.
- Return ONLY what is actually visible in the image. Use \"\" (empty string)
  for fields that are not present. Never invent data.
- If the date can be parsed, always render it as YYYY-MM-DD.
"""


def _clean_json(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in model output.")
    return text[start : end + 1]


def gemini_extract(
    content: bytes,
    mime: str,
    api_key: str,
    model: str,
    doc_hint: str = "",
    timeout: int = 120,
) -> dict:
    hint = f"\nThe file was tagged as: {doc_hint}." if doc_hint else ""
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": GEMINI_PROMPT + hint},
                    {
                        "inline_data": {
                            "mime_type": mime,
                            "data": base64.b64encode(content).decode("ascii"),
                        }
                    },
                ]
            }
        ],
        "generationConfig": {"temperature": 0.0, "responseMimeType": "application/json"},
    }
    resp = requests.post(
        GEMINI_URL.format(model=model, api_key=api_key),
        json=payload,
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Gemini API error {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        raise RuntimeError(f"Gemini returned no content: {json.dumps(data)[:300]}")
    return json.loads(_clean_json(text))


def extract_document(
    content: bytes,
    filename: str,
    config: dict,
    doc_hint: str = "",
) -> dict:
    """Run OCR on one uploaded document and return a normalized record dict."""
    ocr_cfg = config.get("ocr", {})
    engine = ocr_cfg.get("engine", "gemini").lower()
    material = prepare_image(content, filename)

    processed = []
    if isinstance(material, tuple):
        processed.append(material)
    else:
        processed = list(material)

    best = None
    max_dim = int(ocr_cfg.get("max_image_dimension", 2000))
    for img, img_mime in processed:
        img, img_mime = compress_image(img, img_mime, max_dim=max_dim)
        if engine == "gemini" and ocr_cfg.get("gemini_api_key"):
            try:
                best = gemini_extract(
                    img, img_mime, ocr_cfg["gemini_api_key"],
                    ocr_cfg.get("gemini_model", "gemini-2.0-flash"), doc_hint,
                )
            except RuntimeError as exc:
                if "400" in str(exc) or "403" in str(exc) or "invalid" in str(exc).lower():
                    raise
                raise
        else:
            if not ocr_cfg.get("google_vision_api_key"):
                raise RuntimeError(
                    "No OCR credentials configured. Set either "
                    "'ocr.gemini_api_key' or 'ocr.google_vision_api_key' in config.json"
                )
            ann = vision_annotate(img, img_mime, ocr_cfg["google_vision_api_key"])
            best = {"raw_text": ann["text"], "words": ann["words"]}
            best = extract_with_heuristics(ann["text"], doc_hint)
    return best