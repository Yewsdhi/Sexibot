import os

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

PWD = os.getcwd()
database_url = os.getenv("DATABASE_URL", "").strip()

if database_url:
    # Heroku may expose legacy postgres:// URLs.
    if database_url.startswith("postgres://"):
        database_url = "postgresql://" + database_url[len("postgres://") :]
    SQLALCHEMY_DATABASE_URL = database_url
    engine = create_engine(database_url, pool_pre_ping=True)
else:
    os.makedirs(os.path.join(PWD, "data_base"), exist_ok=True)
    SQLALCHEMY_DATABASE_URL = f"sqlite:///{PWD}/data_base/sql_app.db"
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL,
        connect_args={"check_same_thread": False},
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
