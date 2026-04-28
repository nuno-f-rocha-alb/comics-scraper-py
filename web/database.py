import os
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DB_PATH = os.getenv("DB_PATH", "/app/cache/comics.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


def migrate_columns():
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    cols = [c["name"] for c in inspector.get_columns("series")]
    with engine.connect() as conn:
        if "cover_image_url" not in cols:
            conn.execute(text("ALTER TABLE series ADD COLUMN cover_image_url TEXT"))
        if "total_issues" not in cols:
            conn.execute(text("ALTER TABLE series ADD COLUMN total_issues INTEGER"))
        if "metron_annual_series_id" not in cols:
            conn.execute(text("ALTER TABLE series ADD COLUMN metron_annual_series_id INTEGER"))
        if "issue_min" not in cols:
            conn.execute(text("ALTER TABLE series ADD COLUMN issue_min INTEGER NOT NULL DEFAULT 1"))
        else:
            conn.execute(text("UPDATE series SET issue_min = 1 WHERE issue_min IS NULL"))
        if "issue_max" not in cols:
            conn.execute(text("ALTER TABLE series ADD COLUMN issue_max INTEGER"))

        # Recreate monitored_issues to add issue_type + update unique constraint
        tables = inspector.get_table_names()
        if "monitored_issues" in tables:
            mi_cols = [c["name"] for c in inspector.get_columns("monitored_issues")]
            if "issue_type" not in mi_cols:
                conn.execute(text("""
                    CREATE TABLE monitored_issues_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        series_id INTEGER NOT NULL,
                        issue_number TEXT NOT NULL,
                        issue_type TEXT NOT NULL DEFAULT 'regular',
                        UNIQUE(series_id, issue_number, issue_type)
                    )
                """))
                conn.execute(text("""
                    INSERT INTO monitored_issues_new (id, series_id, issue_number, issue_type)
                    SELECT id, series_id, issue_number, 'regular' FROM monitored_issues
                """))
                conn.execute(text("DROP TABLE monitored_issues"))
                conn.execute(text("ALTER TABLE monitored_issues_new RENAME TO monitored_issues"))

        conn.commit()


def init_db():
    from web.models import Series, MetronCache, MetronIssueCache, DownloadJob, MonitoredIssue, AppSetting  # noqa: F401
    Base.metadata.create_all(bind=engine)
    migrate_columns()
