"""Read and write ComicInfo.xml inside CBZ files via comicapi."""
import logging

from comicapi.comicarchive import ComicArchive
from comicapi.genericmetadata import GenericMetadata

CREDIT_ROLES = ["Writer", "Penciller", "Inker", "Colorist", "Letterer", "CoverArtist"]
EDITOR_FIELDS = [
    "Series", "Number", "Title", "Publisher", "Year", "Month", "Web",
    "Writer", "Penciller", "Inker", "Colorist", "Letterer", "CoverArtist",
    "Summary", "Genre", "Tags", "LanguageISO", "PageCount",
]


def empty_fields() -> dict:
    return {k: "" for k in EDITOR_FIELDS}


def _str(v) -> str:
    return "" if v is None else str(v)


def _to_int_or_none(v):
    if v in (None, "", 0):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _credits_by_role(meta) -> dict:
    out: dict[str, list[str]] = {}
    for c in (getattr(meta, "credits", None) or []):
        person = c.get("person") if isinstance(c, dict) else getattr(c, "person", "")
        role = c.get("role") if isinstance(c, dict) else getattr(c, "role", "")
        if not person or not role:
            continue
        # ComicInfo.xml stores cover artist as "Cover"; surface as "CoverArtist" in UI
        if role == "Cover":
            role = "CoverArtist"
        out.setdefault(role, []).append(person)
    return out


def read_comicinfo(cbz_path: str) -> dict:
    """Read fields from ComicInfo.xml inside a CBZ. Empty dict if no metadata."""
    comic = ComicArchive(cbz_path)
    if not comic.has_metadata(1):
        return empty_fields()

    meta = comic.read_metadata(1)
    by_role = _credits_by_role(meta)

    return {
        "Series": _str(meta.series),
        "Number": _str(meta.issue),
        "Title": _str(meta.title),
        "Publisher": _str(meta.publisher),
        "Year": _str(meta.year),
        "Month": _str(meta.month),
        "Web": _str(getattr(meta, "web_link", "")),
        "Writer": ", ".join(by_role.get("Writer", [])),
        "Penciller": ", ".join(by_role.get("Penciller", [])),
        "Inker": ", ".join(by_role.get("Inker", [])),
        "Colorist": ", ".join(by_role.get("Colorist", [])),
        "Letterer": ", ".join(by_role.get("Letterer", [])),
        "CoverArtist": ", ".join(by_role.get("CoverArtist", [])),
        "Summary": _str(getattr(meta, "comments", "") or getattr(meta, "synopsis", "")),
        "Genre": _str(getattr(meta, "genre", "")),
        "Tags": _str(getattr(meta, "tags", "")),
        "LanguageISO": _str(getattr(meta, "language", "")),
        "PageCount": _str(getattr(meta, "page_count", "")),
    }


def write_comicinfo(cbz_path: str, fields: dict) -> bool:
    """Build a GenericMetadata from form fields and write it to the CBZ."""
    comic = ComicArchive(cbz_path)
    meta = GenericMetadata()

    meta.series = fields.get("Series", "") or ""
    meta.issue = fields.get("Number", "") or ""
    meta.title = fields.get("Title", "") or ""
    meta.publisher = fields.get("Publisher", "") or ""
    meta.year = _to_int_or_none(fields.get("Year"))
    meta.month = _to_int_or_none(fields.get("Month"))

    summary = fields.get("Summary", "") or ""
    # comicapi versions vary on which attribute backs ComicInfo.xml <Summary>
    if hasattr(meta, "comments"):
        meta.comments = summary
    if hasattr(meta, "synopsis"):
        meta.synopsis = summary

    if hasattr(meta, "web_link"):
        meta.web_link = fields.get("Web", "") or ""
    if hasattr(meta, "genre"):
        meta.genre = fields.get("Genre", "") or ""
    if hasattr(meta, "tags"):
        meta.tags = fields.get("Tags", "") or ""
    if hasattr(meta, "language"):
        meta.language = fields.get("LanguageISO", "") or ""
    pc = _to_int_or_none(fields.get("PageCount"))
    if pc is not None and hasattr(meta, "page_count"):
        meta.page_count = pc

    for role in CREDIT_ROLES:
        for person in (fields.get(role) or "").split(","):
            person = person.strip()
            if person:
                stored_role = "Cover" if role == "CoverArtist" else role
                meta.add_credit(person, stored_role, primary=False)

    success = comic.write_metadata(meta, 1)
    if success:
        logging.info("ComicInfo.xml updated: %s", cbz_path)
    else:
        logging.error("Failed to write ComicInfo.xml: %s", cbz_path)
    return bool(success)
