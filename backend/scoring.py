# backend/scoring.py
from __future__ import annotations

import os
import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# -----------------------------
# Helpers
# -----------------------------

_WORDS_MAX_EVIDENCE = 25

def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\u00a0", " ")).strip()

def _lower(s: str) -> str:
    return _norm_ws(s).lower()

def _clip_words(s: str, max_words: int = _WORDS_MAX_EVIDENCE) -> str:
    words = _norm_ws(s).split()
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words])

def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except Exception:
        return default

def _ensure_score_range(level_obj: Dict[str, Any], fallback_score: float) -> List[float]:
    if isinstance(level_obj, dict):
        if "score_range" in level_obj and isinstance(level_obj["score_range"], list) and len(level_obj["score_range"]) == 2:
            return [float(level_obj["score_range"][0]), float(level_obj["score_range"][1])]
        if "score_fixed" in level_obj:
            v = float(level_obj["score_fixed"])
            return [v, v]
    return [float(fallback_score), float(fallback_score)]

def _first_nonempty_quote(page_texts: List[str]) -> Optional[Dict[str, Any]]:
    for i, txt in enumerate(page_texts, start=1):
        t = _clip_words(_norm_ws(txt), _WORDS_MAX_EVIDENCE)
        if t:
            return {"quote": t, "page": i}
    return None

def _find_keyword_evidence(
    page_texts: List[str],
    keywords: List[str],
    max_hits: int = 2
) -> List[Dict[str, Any]]:
    """
    Returns list of evidence hits: [{"quote": "...", "page": int}, ...]
    Each quote <= 25 words.
    """
    hits: List[Dict[str, Any]] = []
    if not page_texts or not keywords:
        return hits

    # Pre-normalize keyword list
    kws = [k.strip().lower() for k in keywords if isinstance(k, str) and k.strip()]
    if not kws:
        return hits

    for page_idx, txt in enumerate(page_texts, start=1):
        if not txt:
            continue
        low = txt.lower()
        for kw in kws:
            pos = low.find(kw)
            if pos != -1:
                start = max(0, pos - 140)
                end = min(len(txt), pos + len(kw) + 260)
                snippet = _norm_ws(txt[start:end])
                hits.append({"quote": _clip_words(snippet, _WORDS_MAX_EVIDENCE), "page": page_idx})
                if len(hits) >= max_hits:
                    return hits
    return hits

def _count_min_sections(page_texts: List[str]) -> int:
    """
    Heuristic: count how many key sections appear in the doc.
    Used for floor_soft gate.
    """
    doc = _lower(" \n ".join(page_texts[:20]))  # only first pages are usually enough
    patterns = [
        r"\bmục lục\b",
        r"\bdanh mục minh chứng\b|\bdanh sách minh chứng\b|\bhồ sơ minh chứng\b|\bphụ lục\b",
        r"\btự đánh giá\b|\bphản tư\b|\bnhận xét\b",
        r"\bkế hoạch\b|\bđề xuất\b|\bgiải pháp\b|\bphát triển\b",
        r"\bnghiên cứu khoa học\b|\bsản phẩm nghiên cứu\b|\bbài báo\b|\bdoi\b",
    ]
    count = 0
    for pat in patterns:
        if re.search(pat, doc, flags=re.IGNORECASE):
            count += 1
    return count

# -----------------------------
# LLM Scoring (optional)
# -----------------------------

def _openai_chat_completion(model: str, api_key: str, messages: List[Dict[str, str]], temperature: float = 0.0, timeout_s: int = 45) -> str:
    """
    Minimal OpenAI Chat Completions call using stdlib only (no extra deps).
    If this fails, caller should fallback to rule-based.
    """
    url = "https://api.openai.com/v1/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(payload).encode("utf-8")
    req = Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urlopen(req, timeout=timeout_s) as resp:
        body = resp.read().decode("utf-8")
        obj =
