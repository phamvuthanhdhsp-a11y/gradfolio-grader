from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, Float, ForeignKey, UniqueConstraint
from datetime import datetime

Base = declarative_base()


class Rubric(Base):
    __tablename__ = "rubrics"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    target_use_case = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    versions = relationship("RubricVersion", back_populates="rubric", cascade="all, delete-orphan")


class RubricVersion(Base):
    __tablename__ = "rubric_versions"
    id = Column(Integer, primary_key=True, index=True)
    rubric_id = Column(Integer, ForeignKey("rubrics.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    is_active = Column(Boolean, default=False, index=True)
    config_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    rubric = relationship("Rubric", back_populates="versions")
    evaluations = relationship("Evaluation", back_populates="rubric_version")

    __table_args__ = (
        UniqueConstraint("rubric_id", "version", name="uq_rubric_version"),
    )


class ExtractCache(Base):
    __tablename__ = "extract_cache"
    id = Column(Integer, primary_key=True, index=True)
    file_hash = Column(String, nullable=False, unique=True, index=True)
    file_name = Column(String, nullable=False)
    num_pages = Column(Integer, nullable=True)
    extracted_json = Column(Text, nullable=False)     # includes pages[{page,text,used_ocr}]
    ocr_pages_json = Column(Text, nullable=False)     # list[int]
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class Evaluation(Base):
    __tablename__ = "evaluations"
    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    file_name = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    file_hash = Column(String, nullable=False, index=True)

    rubric_version_id = Column(Integer, ForeignKey("rubric_versions.id"), nullable=False, index=True)

    result_json = Column(Text, nullable=False)  # strict output schema (JSON string)
    total_raw = Column(Float, nullable=False, default=0.0)
    total_adjusted = Column(Float, nullable=False, default=0.0)
    adjustment_notes = Column(Text, nullable=True)

    rubric_version = relationship("RubricVersion", back_populates="evaluations")
