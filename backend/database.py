import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv()

# В Amvera сохраняем БД в монтируемый раздел /data, локально — в ./test.db
DEFAULT_DB = "sqlite:////data/app.db" if os.path.exists("/data") else "sqlite:///./test.db"
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_DB)

connect_args = {"check_same_thread": False} if "sqlite" in DATABASE_URL else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
