from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os

DB_DIR = os.getenv("DB_DIR", "data")
DB_PATH = os.path.join(DB_DIR, "app.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

os.makedirs(DB_DIR, exist_ok=True)

engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
