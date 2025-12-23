import hashlib
import json
import os
import re
import shutil
from datetime import datetime
from typing import Any

import fitz  # PyMuPDF
import pdfplumber
from PIL import Image
import pytesseract

from sqlalchemy.orm import Session
from backend.models import ExtractCache


TEXT_MIN_CHARS = 80
TEXT_MIN_WORDS = 15


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _clean_text(t: str) -> str:
    t = t.replace("\x00", " ").strip()
    t = re.sub(r"[ \t]+", " ", t)
    return t


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def _render_page_to_pil(doc: fitz.Document, page_index: int, zoom: float = 2.0) -> Image.Image:
    page = doc.load_page(page_index)
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    return img


def _ocr_page_pil(img: Image.Image) -> str:
    # Keep raw OCR string; math symbols may be imperfect but preserved as-is
    return pytesseract.image_to_string(img)


def extract_pdf_text_with_ocr(path: str) -> dict[str, Any]:
    """
    Returns:
      {
        "num_pages": int,
        "pages": [{"page":1,"text":"...","used_ocr":bool}],
        "ocr_pages": [int],
        "combined_text": "..."
      }
    """
    use_ocr = tesseract_available()

    pages_out = []
    ocr_pages = []

    # Try PyMuPDF for text
    doc = fitz.open(path)
    num_pages = doc.page_count

    for i in range(num_pages):
        page = doc.load_page(i)
        text = page.get_text("text") or ""
        text = _clean_text(text)
        words = len(text.split())

        used_ocr = False
        if use_ocr and (len(text) < TEXT_MIN_CHARS or words < TEXT_MIN_WORDS):
            try:
                img = _render_page_to_pil(doc, i, zoom=2.0)
                ocr_text = _ocr_page_pil(img)
                ocr_text = _clean_text(ocr_text)
                if len(ocr_text) > len(text):
                    text = ocr_text
                    used_ocr = True
                    ocr_pages.append(i + 1)
            except Exception:
                # OCR failed; keep original extracted text
                pass

        pages_out.append({"page": i + 1, "text": text, "used_ocr": used_ocr})

    doc.close()

    # Fallback: if ALL pages empty, try pdfplumber (some PDFs behave differently)
    if all((p["text"].strip() == "") for p in pages_out):
        try:
            with pdfplumber.open(path) as pdf:
                pages_out = []
                ocr_pages = []
                for i, pg in enumerate(pdf.pages):
                    text = _clean_text(pg.extract_text() or "")
                    pages_out.append({"page": i + 1, "text": text, "used_ocr": False})
        except Exception:
            pass

    combined = "\n\n".join([f"[Page {p['page']}]\n{p['text']}" for p in pages_out])

    return {
        "num_pages": num_pages,
        "pages": pages_out,
        "ocr_pages": ocr_pages,
        "combined_text": combined,
        "ocr_enabled": use_ocr,
    }


def get_or_build_cache(db: Session, file_path: str, file_name: str) -> tuple[str, dict[str, Any], bool]:
    """
    Returns (file_hash, extracted_obj, from_cache)
    """
    file_hash = sha256_file(file_path)
    row = db.query(ExtractCache).filter(ExtractCache.file_hash == file_hash).first()
    if row:
        extracted = json.loads(row.extracted_json)
        return file_hash, extracted, True

    extracted = extract_pdf_text_with_ocr(file_path)

    now = datetime.utcnow()
    cache = ExtractCache(
        file_hash=file_hash,
        file_name=file_name,
        num_pages=extracted.get("num_pages"),
        extracted_json=json.dumps(extracted, ensure_ascii=False),
        ocr_pages_json=json.dumps(extracted.get("ocr_pages", [])),
        created_at=now,
        updated_at=now,
    )
    db.add(cache)
    db.commit()

    return file_hash, extracted, False
