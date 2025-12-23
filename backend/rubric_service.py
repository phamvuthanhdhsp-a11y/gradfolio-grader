import json
import os
from sqlalchemy.orm import Session
from sqlalchemy import desc

from backend.models import Rubric, RubricVersion

DEFAULT_RUBRIC_PATH = os.getenv("DEFAULT_RUBRIC_PATH", "config/rubric.default.json")


def _validate_rubric_config(cfg: dict) -> tuple[bool, str]:
    required_top = ["name", "max_total", "floor_soft", "min_evidence_clos", "clos"]
    for k in required_top:
        if k not in cfg:
            return False, f"Missing key: {k}"

    if not isinstance(cfg["clos"], list) or len(cfg["clos"]) == 0:
        return False, "clos must be a non-empty list"

    for clo in cfg["clos"]:
        for kk in ["clo_id", "title", "levels"]:
            if kk not in clo:
                return False, f"CLO missing key: {kk}"
        levels = clo["levels"]
        for lv in ["1", "2", "3", "4"]:
            if lv not in levels:
                return False, f"{clo['clo_id']} missing level {lv}"
            lv_obj = levels[lv]
            has_fixed = "score_fixed" in lv_obj
            has_range = "score_range" in lv_obj
            if not (has_fixed or has_range):
                return False, f"{clo['clo_id']} level {lv} must have score_fixed or score_range"
            if has_range:
                r = lv_obj["score_range"]
                if (not isinstance(r, list)) or len(r) != 2:
                    return False, f"{clo['clo_id']} level {lv} score_range invalid"
    return True, "ok"


def seed_default_rubric_if_needed(db: Session) -> None:
    exists = db.query(Rubric).first()
    if exists:
        return

    if not os.path.exists(DEFAULT_RUBRIC_PATH):
        raise RuntimeError(f"Missing default rubric file: {DEFAULT_RUBRIC_PATH}")

    with open(DEFAULT_RUBRIC_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    ok, msg = _validate_rubric_config(cfg)
    if not ok:
        raise RuntimeError(f"Default rubric invalid: {msg}")

    rubric = Rubric(
        name=cfg.get("name"),
        description=cfg.get("description"),
        target_use_case=cfg.get("target_use_case"),
    )
    db.add(rubric)
    db.flush()

    version = RubricVersion(
        rubric_id=rubric.id,
        version=1,
        is_active=True,
        config_json=json.dumps(cfg, ensure_ascii=False),
    )
    db.add(version)
    db.commit()


def list_rubrics(db: Session):
    return db.query(Rubric).order_by(Rubric.created_at.desc()).all()


def list_versions(db: Session, rubric_id: int):
    return (
        db.query(RubricVersion)
        .filter(RubricVersion.rubric_id == rubric_id)
        .order_by(desc(RubricVersion.version))
        .all()
    )


def get_active_rubric_version(db: Session) -> RubricVersion | None:
    return db.query(RubricVersion).filter(RubricVersion.is_active == True).first()


def get_rubric_version(db: Session, version_id: int) -> RubricVersion | None:
    return db.query(RubricVersion).filter(RubricVersion.id == version_id).first()


def set_active_version(db: Session, version_id: int) -> None:
    target = get_rubric_version(db, version_id)
    if not target:
        raise ValueError("Rubric version not found")

    # deactivate all
    db.query(RubricVersion).update({RubricVersion.is_active: False})
    target.is_active = True
    db.commit()


def create_rubric(db: Session, name: str, description: str | None, target_use_case: str | None) -> Rubric:
    rubric = Rubric(name=name, description=description, target_use_case=target_use_case)
    db.add(rubric)
    db.commit()
    db.refresh(rubric)
    return rubric


def create_new_version(db: Session, rubric_id: int, config: dict, make_active: bool = False) -> RubricVersion:
    ok, msg = _validate_rubric_config(config)
    if not ok:
        raise ValueError(msg)

    latest = (
        db.query(RubricVersion)
        .filter(RubricVersion.rubric_id == rubric_id)
        .order_by(desc(RubricVersion.version))
        .first()
    )
    next_ver = 1 if not latest else latest.version + 1

    if make_active:
        db.query(RubricVersion).update({RubricVersion.is_active: False})

    rv = RubricVersion(
        rubric_id=rubric_id,
        version=next_ver,
        is_active=make_active,
        config_json=json.dumps(config, ensure_ascii=False),
    )
    db.add(rv)
    db.commit()
    db.refresh(rv)
    return rv


def import_rubric_as_new(db: Session, config: dict, make_active: bool = False) -> RubricVersion:
    # Create Rubric record + version 1
    ok, msg = _validate_rubric_config(config)
    if not ok:
        raise ValueError(msg)

    rubric = Rubric(
        name=config.get("name"),
        description=config.get("description"),
        target_use_case=config.get("target_use_case"),
    )
    db.add(rubric)
    db.flush()

    if make_active:
        db.query(RubricVersion).update({RubricVersion.is_active: False})

    rv = RubricVersion(
        rubric_id=rubric.id,
        version=1,
        is_active=make_active,
        config_json=json.dumps(config, ensure_ascii=False),
    )
    db.add(rv)
    db.commit()
    db.refresh(rv)
    return rv
