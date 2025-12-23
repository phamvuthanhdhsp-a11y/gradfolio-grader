import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Depends, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from sqlalchemy.orm import Session

from backend.db import engine, get_db
from backend.models import Base, Evaluation
from backend import rubric_service
from backend.pdf_extract import get_or_build_cache
from backend.scoring import grade_document

APP_TITLE = "Gradfolio Rubric Grader"
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")

Path(UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
Path("data").mkdir(parents=True, exist_ok=True)

Base.metadata.create_all(bind=engine)

app = FastAPI(title=APP_TITLE)

templates = Jinja2Templates(directory="frontend/templates")
app.mount("/static", StaticFiles(directory="frontend/static"), name="static")


@app.on_event("startup")
def _startup():
    # seed rubric default on first run
    from backend.db import SessionLocal
    db = SessionLocal()
    try:
        rubric_service.seed_default_rubric_if_needed(db)
    finally:
        db.close()


def _load_rubric_cfg(version_row) -> dict:
    return json.loads(version_row.config_json)


@app.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)):
    rubrics = rubric_service.list_rubrics(db)
    versions = []
    for r in rubrics:
        for v in rubric_service.list_versions(db, r.id):
            cfg = json.loads(v.config_json)
            versions.append({
                "version_id": v.id,
                "rubric_name": r.name,
                "version": v.version,
                "is_active": v.is_active,
                "max_total": cfg.get("max_total", 9.0)
            })

    active = rubric_service.get_active_rubric_version(db)
    active_id = active.id if active else (versions[0]["version_id"] if versions else None)

    ocr_enabled = False
    try:
        from backend.pdf_extract import tesseract_available
        ocr_enabled = tesseract_available()
    except Exception:
        ocr_enabled = False

    return templates.TemplateResponse("index.html", {
        "request": request,
        "versions": versions,
        "active_id": active_id,
        "ocr_enabled": ocr_enabled,
        "has_openai_key": bool(os.getenv("OPENAI_API_KEY"))
    })


@app.post("/grade")
async def grade_pdf(
    request: Request,
    pdf: UploadFile = File(...),
    rubric_version_id: int = Form(...),
    db: Session = Depends(get_db)
):
    if not pdf.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a PDF file.")

    version_row = rubric_service.get_rubric_version(db, rubric_version_id)
    if not version_row:
        raise HTTPException(status_code=404, detail="Rubric version not found.")

    # save upload
    safe_name = pdf.filename.replace("/", "_").replace("\\", "_")
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    save_path = os.path.join(UPLOAD_DIR, f"{ts}__{safe_name}")
    with open(save_path, "wb") as f:
        f.write(await pdf.read())

    # extract (with caching)
    file_hash, extracted, from_cache = get_or_build_cache(db, save_path, safe_name)

    rubric_cfg = _load_rubric_cfg(version_row)

    # grade
    scored = grade_document(extracted, rubric_cfg)

    # build strict output schema (E)
    result_obj = {
        "rubric_id": str(version_row.rubric_id),
        "rubric_version": str(version_row.version),
        "file_name": safe_name,
        "clo_results": scored["clo_results"],
        "total_raw": float(scored["total_raw"]),
        "total_adjusted": float(scored["total_adjusted"]),
        "adjustment_notes": scored.get("adjustment_notes", "")
    }

    ev = Evaluation(
        file_name=safe_name,
        file_path=save_path,
        file_hash=file_hash,
        rubric_version_id=version_row.id,
        result_json=json.dumps(result_obj, ensure_ascii=False),
        total_raw=result_obj["total_raw"],
        total_adjusted=result_obj["total_adjusted"],
        adjustment_notes=result_obj.get("adjustment_notes", "")
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)

    return RedirectResponse(url=f"/result/{ev.id}", status_code=303)


@app.get("/result/{evaluation_id}", response_class=HTMLResponse)
def result_page(request: Request, evaluation_id: int, db: Session = Depends(get_db)):
    ev = db.query(Evaluation).filter(Evaluation.id == evaluation_id).first()
    if not ev:
        raise HTTPException(status_code=404, detail="Evaluation not found")

    obj = json.loads(ev.result_json)

    return templates.TemplateResponse("result.html", {
        "request": request,
        "evaluation": ev,
        "result": obj
    })


@app.get("/history", response_class=HTMLResponse)
def history_page(request: Request, db: Session = Depends(get_db)):
    rows = db.query(Evaluation).order_by(Evaluation.created_at.desc()).limit(200).all()
    # load brief
    items = []
    for r in rows:
        try:
            obj = json.loads(r.result_json)
        except Exception:
            obj = {}
        items.append({
            "id": r.id,
            "created_at": r.created_at,
            "file_name": r.file_name,
            "total_raw": r.total_raw,
            "total_adjusted": r.total_adjusted,
            "rubric_version_id": r.rubric_version_id,
            "rubric_version": obj.get("rubric_version")
        })

    return templates.TemplateResponse("history.html", {
        "request": request,
        "items": items
    })


@app.get("/rubrics", response_class=HTMLResponse)
def rubric_manager(request: Request, db: Session = Depends(get_db)):
    rubrics = rubric_service.list_rubrics(db)

    data = []
    for r in rubrics:
        versions = rubric_service.list_versions(db, r.id)
        vrows = []
        for v in versions:
            cfg = json.loads(v.config_json)
            vrows.append({
                "id": v.id,
                "version": v.version,
                "is_active": v.is_active,
                "max_total": cfg.get("max_total", 9.0),
                "name": cfg.get("name", r.name),
            })
        data.append({
            "rubric": r,
            "versions": vrows
        })

    active = rubric_service.get_active_rubric_version(db)
    active_id = active.id if active else None

    return templates.TemplateResponse("rubric.html", {
        "request": request,
        "data": data,
        "active_id": active_id
    })


# -------------------------
# API endpoints (Swagger)
# -------------------------

@app.get("/api/rubrics")
def api_list_rubrics(db: Session = Depends(get_db)):
    rubrics = rubric_service.list_rubrics(db)
    out = []
    for r in rubrics:
        out.append({
            "id": r.id,
            "name": r.name,
            "description": r.description,
            "target_use_case": r.target_use_case,
            "created_at": r.created_at.isoformat()
        })
    return out


@app.get("/api/rubrics/{rubric_id}/versions")
def api_list_versions(rubric_id: int, db: Session = Depends(get_db)):
    versions = rubric_service.list_versions(db, rubric_id)
    out = []
    for v in versions:
        cfg = json.loads(v.config_json)
        out.append({
            "id": v.id,
            "rubric_id": v.rubric_id,
            "version": v.version,
            "is_active": v.is_active,
            "config": cfg,
            "created_at": v.created_at.isoformat()
        })
    return out


@app.post("/api/rubrics")
def api_create_rubric(payload: dict, db: Session = Depends(get_db)):
    name = payload.get("name")
    if not name:
        raise HTTPException(400, "name is required")
    r = rubric_service.create_rubric(db, name, payload.get("description"), payload.get("target_use_case"))
    return {"id": r.id}


@app.post("/api/rubrics/{rubric_id}/versions")
def api_create_version(rubric_id: int, payload: dict, db: Session = Depends(get_db)):
    make_active = bool(payload.get("make_active", False))
    config = payload.get("config")
    if not isinstance(config, dict):
        raise HTTPException(400, "config must be an object")
    try:
        rv = rubric_service.create_new_version(db, rubric_id, config, make_active=make_active)
        return {"id": rv.id, "version": rv.version, "is_active": rv.is_active}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/rubric_versions/{version_id}/set_active")
def api_set_active(version_id: int, db: Session = Depends(get_db)):
    try:
        rubric_service.set_active_version(db, version_id)
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/api/rubric_versions/{version_id}/export")
def api_export_version(version_id: int, db: Session = Depends(get_db)):
    v = rubric_service.get_rubric_version(db, version_id)
    if not v:
        raise HTTPException(404, "not found")
    cfg = json.loads(v.config_json)
    return JSONResponse(cfg)


@app.post("/api/rubrics/import")
async def api_import_rubric(make_active: bool = Form(False), file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename.lower().endswith(".json"):
        raise HTTPException(400, "Please upload a JSON file.")
    raw = await file.read()
    try:
        cfg = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(400, "Invalid JSON.")
    try:
        rv = rubric_service.import_rubric_as_new(db, cfg, make_active=bool(make_active))
        return {"rubric_id": rv.rubric_id, "version_id": rv.id, "version": rv.version, "is_active": rv.is_active}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/evaluations")
def api_list_evaluations(db: Session = Depends(get_db)):
    rows = db.query(Evaluation).order_by(Evaluation.created_at.desc()).limit(200).all()
    return [{
        "id": r.id,
        "created_at": r.created_at.isoformat(),
        "file_name": r.file_name,
        "rubric_version_id": r.rubric_version_id,
        "total_raw": r.total_raw,
        "total_adjusted": r.total_adjusted
    } for r in rows]


@app.get("/api/evaluations/{evaluation_id}")
def api_get_evaluation(evaluation_id: int, db: Session = Depends(get_db)):
    ev = db.query(Evaluation).filter(Evaluation.id == evaluation_id).first()
    if not ev:
        raise HTTPException(404, "not found")
    return json.loads(ev.result_json)


@app.get("/api/evaluations/{evaluation_id}/report.json")
def api_download_report_json(evaluation_id: int, db: Session = Depends(get_db)):
    ev = db.query(Evaluation).filter(Evaluation.id == evaluation_id).first()
    if not ev:
        raise HTTPException(404, "not found")
    obj = json.loads(ev.result_json)
    return JSONResponse(obj, headers={
        "Content-Disposition": f'attachment; filename="report_{evaluation_id}.json"'
    })


@app.get("/api/evaluations/{evaluation_id}/report.html")
def api_download_report_html(evaluation_id: int, db: Session = Depends(get_db)):
    ev = db.query(Evaluation).filter(Evaluation.id == evaluation_id).first()
    if not ev:
        raise HTTPException(404, "not found")

    obj = json.loads(ev.result_json)
    # Render a simple HTML report on the fly
    rows = ""
    for c in obj.get("clo_results", []):
        evs = "".join([f"<li><b>p.{e.get('page')}</b>: {e.get('quote')}</li>" for e in c.get("evidence", [])])
        rows += f"""
        <tr>
          <td>{c.get('clo_id')}</td>
          <td>{c.get('title')}</td>
          <td style="text-align:center">{c.get('level')}</td>
          <td style="text-align:right">{c.get('score')}</td>
          <td><ul>{evs}</ul></td>
          <td>{c.get('rationale')}</td>
        </tr>
        """

    html = f"""
    <!doctype html>
    <html>
    <head>
      <meta charset="utf-8"/>
      <title>Report {evaluation_id}</title>
      <style>
        body{{font-family: Arial, sans-serif; margin: 24px;}}
        table{{border-collapse: collapse; width: 100%;}}
        th,td{{border:1px solid #ddd; padding:8px; vertical-align: top;}}
        th{{background:#f5f5f5;}}
        .totals{{margin-top:16px; padding:12px; background:#fafafa; border:1px solid #eee;}}
      </style>
    </head>
    <body>
      <h2>Report chấm hồ sơ tốt nghiệp</h2>
      <p><b>File:</b> {obj.get("file_name")}<br/>
      <b>Rubric:</b> rubric_id={obj.get("rubric_id")}, version={obj.get("rubric_version")}</p>

      <table>
        <thead>
          <tr>
            <th>CLO</th><th>Tiêu chí</th><th>Level</th><th>Score</th><th>Evidence</th><th>Rationale</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>

      <div class="totals">
        <b>Total raw:</b> {obj.get("total_raw")}<br/>
        <b>Total adjusted:</b> {obj.get("total_adjusted")}<br/>
        <b>Adjustment notes:</b> {obj.get("adjustment_notes")}
      </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html, headers={
        "Content-Disposition": f'attachment; filename="report_{evaluation_id}.html"'
    })
