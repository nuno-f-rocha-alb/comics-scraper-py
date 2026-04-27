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
        )

    def __repr__(self) -> str:
        return f"<Series {self.publisher}/{self.series_name} ({self.year})>"


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
