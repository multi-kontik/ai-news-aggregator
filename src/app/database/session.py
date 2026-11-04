from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database.models import Base

engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine)


def create_tables() -> list[str]:
    """Create any tables that don't exist yet (existing ones are left untouched) and return all table names."""
    Base.metadata.create_all(engine)
    return list(Base.metadata.tables)
