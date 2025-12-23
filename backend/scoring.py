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
        obj = json.loads(body)
        # chat.completions format
        return obj["choices"][0]["message"]["content"]

def _llm_score_document(
    rubric: Dict[str, Any],
    page_texts: List[str],
    num_pages: int
) -> Optional[Dict[str, Any]]:
    """
    Returns parsed JSON object from LLM if success, else None.
    """
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"

    # Provide page-delimited text (truncate to keep request reasonable)
    # Keep first N pages + last few pages (often includes appendices)
    head_n = min(12, len(page_texts))
    tail_n = min(4, max(0, len(page_texts) - head_n))
    chosen_pages: List[Tuple[int, str]] = []
    for i in range(head_n):
        chosen_pages.append((i + 1, page_texts[i]))
    if tail_n > 0:
        for j in range(tail_n):
            idx = len(page_texts) - tail_n + j
            chosen_pages.append((idx + 1, page_texts[idx]))

    doc_chunks = []
    for p, t in chosen_pages:
        t2 = _norm_ws(t)
        if len(t2) > 4000:
            t2 = t2[:4000]  # hard cap
        doc_chunks.append(f"[PAGE {p}]\n{t2}")

    doc_text = "\n\n".join(doc_chunks)

    schema_hint = {
        "clo_results": [
            {
                "clo_id": "CLO1 or TOTAL",
                "title": "string",
                "level": "int (1-4)",
                "score": "float",
                "score_range": [0.0, 0.0],
                "evidence": [{"quote": "<=25 words", "page": 1}],
                "rationale": "string"
            }
        ],
        "total_raw": 0.0
    }

    system = (
        "You are a strict grading engine. Output MUST be valid JSON only. "
        "No markdown, no extra keys beyond those requested."
    )

    user = (
        "Grade the PDF content using the provided rubric JSON.\n\n"
        "Rubric JSON:\n"
        f"{json.dumps(rubric, ensure_ascii=False)}\n\n"
        f"PDF pages extracted (num_pages={num_pages}):\n{doc_text}\n\n"
        "Return STRICT JSON with keys: clo_results (list) and total_raw (float).\n"
        "Each clo_result must include: clo_id, title, level (1-4), score, score_range [min,max], "
        "evidence (list of {quote,page}), rationale.\n"
        "Evidence quotes must be <= 25 words. If you cite, include correct page.\n"
        f"Schema hint:\n{json.dumps(schema_hint, ensure_ascii=False)}"
    )

    try:
        content = _openai_chat_completion(
            model=model,
            api_key=api_key,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.0,
            timeout_s=45,
        )
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            return None
        if "clo_results" not in parsed:
            return None
        return parsed
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, KeyError, ValueError):
        return None

# -----------------------------
# Rule-based scoring
# -----------------------------

def _score_total_rule_based(
    rubric: Dict[str, Any],
    page_texts: List[str],
    num_pages: int
) -> Dict[str, Any]:
    """
    Rubric with single CLO: clo_id == 'TOTAL'
    Implements your business rules.
    """
    clo = rubric["clos"][0]
    levels = clo.get("levels", {}) or {}
    meta = clo.get("meta", {}) or {}

    evidence_list_keywords = meta.get("evidence_list_keywords", [
        "danh mục minh chứng", "danh sách minh chứng", "hồ sơ minh chứng", "phụ lục", "mã minh chứng"
    ])
    special_keywords = meta.get("special_evidence_keywords", [
        "hoạt động nghiên cứu khoa học", "nghiên cứu khoa học", "sản phẩm nghiên cứu khoa học",
        "bài báo", "doi", "sáng kiến", "sáng kiến kinh nghiệm", "giải thưởng", "award", "publication"
    ])
    leadership_keywords = meta.get("leadership_keywords_strict", meta.get("leadership_keywords", [
        "lớp trưởng", "nhóm trưởng", "ban cán sự", "bí thư", "lớp phó", "trưởng nhóm"
    ]))

    ev_list = _find_keyword_evidence(page_texts, evidence_list_keywords, max_hits=2)
    ev_special = _find_keyword_evidence(page_texts, special_keywords, max_hits=2)
    ev_lead = _find_keyword_evidence(page_texts, leadership_keywords, max_hits=2)

    score = 6.5
    level = 1
    rationale_parts: List[str] = []

    # Base: evidence list => 7.0
    if ev_list:
        score = 7.0
        level = 2
        rationale_parts.append("Có Danh mục/Danh sách minh chứng → 7.0")

        # page bonus rules (only after evidence list)
        if num_pages and num_pages < 30:
            score = max(score, 7.5)
            level = max(level, 3)
            rationale_parts.append(f"Số trang {num_pages} < 30 → +0.5 = 7.5")

        if num_pages and num_pages > 50:
            score = max(score, 8.0)
            level = max(level, 3)
            rationale_parts.append(f"Số trang {num_pages} > 50 → +1.0 = 8.0")

    # special evidence overrides page bonus
    if ev_special:
        score = max(score, 8.5)
        level = 4
        rationale_parts.append("Có minh chứng đặc biệt (NCKH/bài báo/sáng kiến/giải thưởng…) → 8.5")

    # leadership overrides special
    if ev_lead:
        score = max(score, 9.0)
        level = 4
        rationale_parts.append("Có vai trò lớp trưởng/nhóm trưởng/ban cán sự… → 9.0")

    evidence: List[Dict[str, Any]] = []
    # Prefer showing the strongest evidence first
    if ev_lead:
        evidence.extend(ev_lead)
    if ev_special:
        evidence.extend(ev_special)
    if ev_list:
        evidence.extend(ev_list)

    if not evidence:
        q = _first_nonempty_quote(page_texts)
        if q:
            evidence = [q]

    chosen_level_obj = levels.get(str(level), {})
    score_range = _ensure_score_range(chosen_level_obj, score)

    rationale = " | ".join(rationale_parts) if rationale_parts else "Heuristic: ít dấu hiệu/thiếu phần trọng tâm."

    return {
        "clo_id": clo.get("clo_id", "TOTAL"),
        "title": clo.get("title", "Điểm tổng hồ sơ tốt nghiệp"),
        "level": int(level),
        "score": float(score),
        "score_range": [float(score_range[0]), float(score_range[1])],
        "evidence": evidence[:2],
        "rationale": rationale
    }

def _score_clo14_rule_based(
    rubric: Dict[str, Any],
    page_texts: List[str],
    num_pages: int
) -> List[Dict[str, Any]]:
    """
    Simple heuristic for legacy CLO1..CLO4 rubric.
    """
    doc = _lower(" \n ".join(page_texts))

    def kw_hit(keywords: List[str]) -> int:
        c = 0
        for k in keywords:
            if k and k.lower() in doc:
                c += 1
        return c

    results: List[Dict[str, Any]] = []
    for clo in rubric.get("clos", []):
        clo_id = clo.get("clo_id", "CLO?")
        title = clo.get("title", "")
        levels = clo.get("levels", {}) or {}

        # Very rough keyword sets per CLO
        if clo_id.upper() == "CLO1":
            hits = kw_hit(["kế hoạch", "chiến lược", "nguồn lực", "minh chứng", "thu thập", "công cụ", "dữ liệu"])
        elif clo_id.upper() == "CLO2":
            hits = kw_hit(["tự đánh giá", "phản tư", "thích ứng", "cải tiến", "giai đoạn", "phát triển", "mục tiêu"])
        elif clo_id.upper() == "CLO3":
            hits = kw_hit(["mục lục", "bố cục", "trình bày", "kết luận", "tóm tắt", "logic", "rõ ràng"])
        elif clo_id.upper() == "CLO4":
            hits = kw_hit(["swot", "điểm mạnh", "điểm yếu", "cơ hội", "thách thức", "giải pháp", "biện pháp"])
        else:
            hits = kw_hit(["minh chứng", "mục lục", "tự đánh giá", "kế hoạch"])

        # Map hits -> level
        if hits >= 6:
            level = 4
        elif hits >= 4:
            level = 3
        elif hits >= 2:
            level = 2
        else:
            level = 1

        level_obj = levels.get(str(level), {})
        sr = _ensure_score_range(level_obj, 0.0)

        # Choose a score: midpoint for ranges, or fixed
        if "score_fixed" in level_obj:
            score = float(level_obj["score_fixed"])
        elif "score_range" in level_obj and isinstance(level_obj["score_range"], list) and len(level_obj["score_range"]) == 2:
            score = (float(level_obj["score_range"][0]) + float(level_obj["score_range"][1])) / 2.0
        else:
            score = float(sr[0])

        # Evidence: use keyword hit evidence from description words if possible
        ev = _first_nonempty_quote(page_texts)
        evidence = [ev] if ev else []

        results.append({
            "clo_id": clo_id,
            "title": title,
            "level": int(level),
            "score": float(score),
            "score_range": [float(sr[0]), float(sr[1])],
            "evidence": evidence[:1],
            "rationale": f"Heuristic hits={hits} → level {level}."
        })

    return results

# -----------------------------
# Normalization / Adjustment
# -----------------------------

def _apply_normalization(
    rubric: Dict[str, Any],
    clo_results: List[Dict[str, Any]],
    page_texts: List[str]
) -> Tuple[float, float, str]:
    """
    total_raw = sum(scores)
    total_adjusted = min(total_raw, max_total)
    floor_soft applies only if:
      - evidence_clos >= K
      - sections_ok (heuristic count)
    """
    max_total = _safe_float(rubric.get("max_total", 9.0), 9.0)
    floor_soft = _safe_float(rubric.get("floor_soft", 4.5), 4.5)
    min_evidence_clos = int(rubric.get("min_evidence_clos", 3) or 3)

    total_raw = sum(_safe_float(r.get("score", 0.0), 0.0) for r in clo_results)
    total_adjusted = min(total_raw, max_total)

    evidence_clos = 0
    for r in clo_results:
        ev = r.get("evidence", [])
        if isinstance(ev, list) and len(ev) > 0:
            evidence_clos += 1

    sections_count = _count_min_sections(page_texts)
    sections_ok = sections_count >= 3

    notes = [f"Cap max_total={max_total}."]
    if total_adjusted < floor_soft:
        if evidence_clos >= min_evidence_clos and sections_ok:
            total_adjusted = floor_soft
            notes.append(
                f"Applied floor_soft={floor_soft} because evidence_clos={evidence_clos}>=K={min_evidence_clos} "
                f"and sections_ok (count={sections_count})."
            )
        else:
            notes.append(
                f"Did not apply floor_soft={floor_soft} because evidence_clos={evidence_clos} (K={min_evidence_clos}) "
                f"or sections_ok={sections_ok} (count={sections_count})."
            )

    return float(total_raw), float(total_adjusted), " ".join(notes)

# -----------------------------
# Public entry point
# -----------------------------

def score_document(
    rubric: Dict[str, Any],
    file_name: str,
    page_texts: List[str],
    num_pages: int,
    rubric_id: Optional[str] = None,
    rubric_version: Optional[str] = None
) -> Dict[str, Any]:
    """
    Returns final JSON result in required schema.
    """
    started = time.time()
    rubric_id = rubric_id or str(rubric.get("id", ""))
    rubric_version = rubric_version or str(rubric.get("version", rubric.get("version_id", "")))

    # 1) Try LLM if key exists
    llm_payload = _llm_score_document(rubric, page_texts, num_pages)
    clo_results: List[Dict[str, Any]] = []
    llm_used = False
    llm_error_note = ""

    if llm_payload:
        try:
            # Expect: {"clo_results":[...], "total_raw": ...}
            if isinstance(llm_payload.get("clo_results"), list):
                clo_results = llm_payload["clo_results"]
                llm_used = True
        except Exception:
            llm_used = False
            llm_error_note = "LLM parsing failed; fallback to rule-based."

    # 2) Rule-based fallback
    if not clo_results:
        if isinstance(rubric.get("clos"), list) and len(rubric["clos"]) == 1 and rubric["clos"][0].get("clo_id") == "TOTAL":
            clo_results = [_score_total_rule_based(rubric, page_texts, num_pages)]
        else:
            clo_results = _score_clo14_rule_based(rubric, page_texts, num_pages)

    # 3) Normalize
    total_raw, total_adjusted, adjustment_notes = _apply_normalization(rubric, clo_results, page_texts)

    if llm_error_note:
        adjustment_notes = f"{adjustment_notes} {llm_error_note}"
    if llm_used:
        adjustment_notes = f"{adjustment_notes} LLM_used=true."

    result = {
        "rubric_id": rubric_id,
        "rubric_version": rubric_version,
        "file_name": file_name,
        "clo_results": clo_results,
        "total_raw": float(total_raw),
        "total_adjusted": float(total_adjusted),
        "adjustment_notes": adjustment_notes
    }

    # Optional timing info for debugging (kept minimal; remove if you want)
    _elapsed = time.time() - started
    # You can uncomment this if you want to store timing somewhere:
    # result["_elapsed_s"] = round(_elapsed, 3)

    return result
