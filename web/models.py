from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from web.database import Base







class Series(Base):
    __tablename__ = "series"
    __table_args__ = (UniqueConstraint("publisher", "series_name", "year", name="uq_series"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    publisher: Mapped[str] = mapped_column(String, nullable=False)
    series_name: Mapped[str] = mapped_column(String, nullable=False)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)

    comicvine_volume_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metron_series_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Annual: separate CV volume; Metron annuals are auto-detected from parent
    annual_comicvine_volume_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Override the search term used on getcomics.org (defaults to series_name at query time)
    getcomics_search_name: Mapped[str | None] = mapped_column(String, nullable=True)

    # Lower bound — scraper ignores issues below this number (default 1)
    issue_min: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    # issue_max column exists in DB but is unused — upper bound comes from total_issues

    metron_annual_series_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    cover_image_url: Mapped[str | None] = mapped_column(String, nullable=True)
    total_issues: Mapped[int | None] = mapped_column(Integer, nullable=True)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )

    def to_scraper_tuple(self) -> tuple:
        """Returns the tuple shape the scraper expects.

        [0] publisher  [1] series_name  [2] year  [3] cv_id  [4] annual_cv_id
        [5] metron_series_id  [6] getcomics_search_name
        """
        return (
            self.publisher,
            self.series_name,
            str(self.year) if self.year is not None else "",
            str(self.comicvine_volume_id) if self.comicvine_volume_id is not None else None,
            str(self.annual_comicvine_volume_id) if self.annual_comicvine_volume_id is not None else None,
            str(self.metron_series_id) if self.metron_series_id is not None else None,
            self.getcomics_search_name or None,
            self.issue_min,    # [7] — manual lower bound
            self.total_issues, # [8] — Metron upper bound (updated by Sync Covers)
        )

    def __repr__(self) -> str:
        return f"<Series {self.publisher}/{self.series_name} ({self.year})>"


class MonitoredIssue(Base):
    """Explicit issue-level monitoring. When any rows exist for a series,
    the scraper only downloads issues listed here (selective mode).
    When no rows exist, all issues are downloaded (default mode)."""
    __tablename__ = "monitored_issues"
    __table_args__ = (UniqueConstraint("series_id", "issue_number", name="uq_monitored_issue"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    series_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    issue_number: Mapped[str] = mapped_column(String, nullable=False)


class DownloadJob(Base):
    __tablename__ = "download_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    series_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    issue_number: Mapped[str] = mapped_column(String, nullable=False)
    search_term: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="queued")
    filename: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<DownloadJob series={self.series_id} issue={self.issue_number} status={self.status}>"


class MetronCache(Base):
    """Local mirror of Metron series metadata — avoids repeated API calls."""
    __tablename__ = "metron_cache"
    __table_args__ = (Index("ix_metron_cache_name", "name"),)

    metron_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    publisher_name: Mapped[str | None] = mapped_column(String, nullable=True)
    year_began: Mapped[int | None] = mapped_column(Integer, nullable=True)
    issue_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    series_type: Mapped[str | None] = mapped_column(String, nullable=True)
    cv_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String, nullable=True)
    cached_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )


class MetronIssueCache(Base):
    """Local mirror of Metron issue list per series — avoids repeated API calls."""
    __tablename__ = "metron_issue_cache"
    __table_args__ = (Index("ix_metron_issue_cache_series", "series_id"),)

    metron_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    series_id: Mapped[int] = mapped_column(Integer, nullable=False)
    number: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    cover_date: Mapped[str | None] = mapped_column(String, nullable=True)
    store_date: Mapped[str | None] = mapped_column(String, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String, nullable=True)
    cached_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )
