import os

# SQLite for local dev/tests; set DATABASE_URL to a PostgreSQL URL in production,
# e.g. postgresql+psycopg://user:pass@host:5432/agent_factory
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./agent_factory.db")
