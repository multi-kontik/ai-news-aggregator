"""Create all database tables.

Run with: uv run python scripts/create_tables.py
"""

from app.database.models import Base
from app.database.session import engine

if __name__ == "__main__":
    Base.metadata.create_all(engine)
    print(f"Created tables: {', '.join(Base.metadata.tables)}")
