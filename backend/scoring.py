import json
import os
import re
from typing import Any

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")  # override if you like


def _word_truncate(text: str, max_words: int = 25) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text.strip()
    return " ".join(words[:max_words]).strip()


def _pick_score(level_def: dict) -> tuple[float, list[float]]:
    if "score_fixed" in level_def:
        s = float(level_def["score_fixed"])
        return s, [s, s]
    r = level_def.get("score_range", [0.0, 0.0])
    mn = float(r[0])
    mx = float(r[1])
    # choose midpoint
    return round((mn + mx) / 2.0, 2), [mn, mx]


def _get_level_def(clo: dict, level: int) -> dict:
    return clo["levels"][str(level)]


def _detect_sections(extracted_pages: list[dict]) -> dict[str, bool]:
    text = "\n".join([p.get("text", "") for p in extracted_pages]).lower()

    def has_any(keys):
        return any(k in text for k in keys)

    return {
        "toc": has_any(["mục lục", "table of contents", "contents"]),
        "self_eval": has_any(["tự đánh giá", "self-assessment", "phản tư", "reflection"]),
        "evidence": has_any(["minh chứng", "evidence", "phụ lục", "appendix", "đính kèm"]),
        "swot": has_any(["swot", "điểm mạnh", "điểm yếu", "cơ hội", "thách thức"]),
        "plan": has_any(["kế hoạch", "biện pháp", "hành động", "action plan", "định hướng", "phát triển bản thân"])
    }


def _find_evidence(pages: list[dict], keywords: list[str], max_items: int = 2) -> list[dict]:
    out = []
    rx = re.compile("|".join([re.escape(k) for k in keywords if k.strip()]), re.IGNORECASE) if keywords else None

    for p in pages:
        page_no = p.get("page")
        txt = p.get("text", "")
        if not txt.strip():
            continue

        lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
        for ln in lines:
            if rx and rx.search(ln):
                quote = _word_truncate(ln, 25)
                out.append({"quote": quote, "page": int(page_no) if page_no else None})
                if len(out) >= max_items:
                    return out

    # fallback: take first non-empty line as evidence if none found
    if not out:
        for p in pages:
            lines = [ln.strip() for ln in p.get("text", "").splitlines() if ln.strip()]
            if lines:
                out.append({"quote": _word_truncate(lines[0], 25), "page": int(p.get("page")) if p.get("page") else None})
                break

    return out


def heuristic_grade(extracted: dict, rubric_cfg: dict) -> dict:
    pages = extracted.get("pages", [])
    combined = extracted.get("combined_text", "")
    total_chars = len(combined)
    sections = _detect_sections(pages)

    clo_keywords = {
        "CLO1": ["nguồn lực", "công nghệ", "dữ liệu", "minh chứng", "thu thập", "kế hoạch", "evidence"],
        "CLO2": ["tự đánh giá", "phản tư", "thích ứng", "cải tiến", "triết lí", "lý tưởng", "năng lực", "giai đoạn"],
        "CLO3": ["mục lục", "bố cục", "trình bày", "logic", "ngôn ngữ", "định dạng", "biểu đồ", "bảng"],
        "CLO4": ["swot", "điểm mạnh", "điểm yếu", "cơ hội", "thách thức", "biện pháp", "kế hoạch phát triển"]
    }

    def count_hits(keys: list[str]) -> int:
        t = combined.lower()
        return sum(t.count(k.lower()) for k in keys)

    clo_results = []
    evidence_clos = 0

    for clo in rubric_cfg["clos"]:
        cid = clo["clo_id"]
        keys = clo_keywords.get(cid, [])
        hits = count_hits(keys)

        # rough signals
        has_section_bonus = 0
        if cid == "CLO1" and sections["evidence"]:
            has_section_bonus += 2
        if cid == "CLO2" and sections["self_eval"]:
            has_section_bonus += 2
        if cid == "CLO3" and sections["toc"]:
            has_section_bonus += 2
        if cid == "CLO4" and sections["swot"]:
            has_section_bonus += 2

        length_bonus = 2 if total_chars > 9000 else (1 if total_chars > 4000 else 0)

        signal = hits + has_section_bonus + length_bonus

        # map to level
        if signal >= 10:
            level = 4
            rationale = "Heuristic: nhiều dấu hiệu phù hợp (từ khóa + cấu trúc/độ dài), khả năng đáp ứng tốt."
        elif signal >= 6:
            level = 3
            rationale = "Heuristic: có dấu hiệu khá rõ nhưng chưa đủ mạnh/đầy đủ."
        elif signal >= 3:
            level = 2
            rationale = "Heuristic: có một phần dấu hiệu nhưng còn mỏng, thiếu liên kết."
        else:
            level = 1
            rationale = "Heuristic: ít dấu hiệu/thiếu phần trọng tâm."

        ev = _find_evidence(pages, keys, max_items=2)
        if ev and any(e.get("quote") for e in ev):
            evidence_clos += 1

        level_def = _get_level_def(clo, level)
        score, score_range = _pick_score(level_def)

        clo_results.append({
            "clo_id": cid,
            "title": clo["title"],
            "level": level,
            "score": float(score),
            "score_range": [float(score_range[0]), float(score_range[1])],
            "evidence": ev,
            "rationale": rationale
        })

    # totals + normalization
    max_total = float(rubric_cfg.get("max_total", 9.0))
    floor_soft = float(rubric_cfg.get("floor_soft", 4.5))
    min_evidence_clos = int(rubric_cfg.get("min_evidence_clos", 3))

    total_raw = round(sum(x["score"] for x in clo_results), 2)
    total_adjusted = min(total_raw, max_total)
    adjustment_notes = f"Cap max_total={max_total}. "

    # floor rule
    section_count = sum(1 for v in sections.values() if v)
    enough_sections = section_count >= 3  # heuristic minimal completeness
    if total_adjusted < floor_soft:
        if evidence_clos >= min_evidence_clos and enough_sections:
            total_adjusted = min(floor_soft, max_total)
            adjustment_notes += f"Applied floor_soft={floor_soft} because evidence_clos={evidence_clos}>=K={min_evidence_clos} and sections_ok (count={section_count})."
        else:
            adjustment_notes += f"Did NOT apply floor_soft={floor_soft} because evidence_clos={evidence_clos} (K={min_evidence_clos}) or sections_ok={enough_sections} (count={section_count})."
    else:
        adjustment_notes += "No floor adjustment needed."

    return {
        "clo_results": clo_results,
        "total_raw": float(total_raw),
        "total_adjusted": float(round(total_adjusted, 2)),
        "adjustment_notes": adjustment_notes,
        "sections_detected": sections,
        "evidence_clos": evidence_clos
    }


def _openai_available() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def llm_grade(extracted: dict, rubric_cfg: dict) -> dict | None:
    """
    Returns strict schema object (without outer metadata fields),
    or None on failure (caller will fallback).
    """
    if not _openai_available():
        return None

    try:
        from openai import OpenAI
        client = OpenAI()
    except Exception:
        return None

    pages = extracted.get("pages", [])
    # Keep prompt bounded
    joined = "\n\n".join([f"[Page {p['page']}] {p.get('text','')}" for p in pages])
    joined = joined[:25000]  # trim to limit tokens

    schema_hint = {
        "clo_results": [
            {
                "clo_id": "CLO1",
                "title": "string",
                "level": 1,
                "score": 0.0,
                "score_range": [0.0, 0.0],
                "evidence": [{"quote": "<=25 words", "page": 1}],
                "rationale": "string"
            }
        ],
        "total_raw": 0.0,
        "total_adjusted": 0.0,
        "adjustment_notes": "string"
    }

    system = (
        "Bạn là giám khảo chấm hồ sơ theo rubric. "
        "Bạn PHẢI trả về JSON HỢP LỆ (không markdown, không giải thích ngoài JSON). "
        "Evidence quote tối đa 25 từ mỗi trích dẫn. Nếu có số trang thì page là số nguyên, nếu không thì null. "
        "Chọn level 1-4 theo mô tả level. Score phải nằm trong score_range hoặc đúng score_fixed theo rubric."
    )

    user = {
        "rubric": rubric_cfg,
        "document_pages_text": joined,
        "instructions": [
            "Chấm CLO1..CLO4 theo rubric.",
            "Trả về đúng schema JSON (giống ví dụ schema_hint), chỉ các trường đó.",
            "total_raw = tổng score CLO; total_adjusted áp dụng cap max_total và floor_soft theo quy tắc trong rubric_cfg: "
            "cap max_total, floor_soft chỉ áp dụng nếu có evidence tối thiểu cho >=K CLOs (K=min_evidence_clos) và tài liệu có đủ phần tối thiểu."
        ],
        "schema_hint": schema_hint
    }

    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)}
            ],
            response_format={"type": "json_object"},
            temperature=0.2
        )
        content = resp.choices[0].message.content
        obj = json.loads(content)

        # minimal validation
        if "clo_results" not in obj or "total_raw" not in obj or "total_adjusted" not in obj or "adjustment_notes" not in obj:
            return None
        return obj
    except Exception:
        return None


def grade_document(extracted: dict, rubric_cfg: dict) -> dict:
    """
    Returns:
      {
        "clo_results": [...],
        "total_raw": float,
        "total_adjusted": float,
        "adjustment_notes": str
      }
    """
    llm_obj = llm_grade(extracted, rubric_cfg)
    if llm_obj:
        # Ensure normalization rules still respected (safety net)
        # If LLM violated, fall back to heuristic.
        try:
            for clo in llm_obj["clo_results"]:
                if not (1 <= int(clo["level"]) <= 4):
                    raise ValueError("bad level")
                if not isinstance(clo.get("evidence", []), list):
                    raise ValueError("bad evidence")
            # trust but verify totals are numbers
            float(llm_obj["total_raw"])
            float(llm_obj["total_adjusted"])
            return llm_obj
        except Exception:
            pass

    # fallback
    h = heuristic_grade(extracted, rubric_cfg)
    return {
        "clo_results": h["clo_results"],
        "total_raw": h["total_raw"],
        "total_adjusted": h["total_adjusted"],
        "adjustment_notes": h["adjustment_notes"]
    }
