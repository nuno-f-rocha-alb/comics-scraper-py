"""Build a ComicRack CBL (reading list) that Komga can import.

Komga matches Book entries to its library by Series name + Number, so Series
must equal what Komga sees (= ComicInfo Series = Metron name, which the app tags).
The full reading order is emitted; Komga simply skips books it can't match.
"""
from xml.sax.saxutils import quoteattr


def build_cbl(name: str, items) -> str:
    """items: ReadingListItem rows (already ordered). Returns CBL XML text."""
    books = []
    for it in items:
        attrs = (
            f"Series={quoteattr(it.series_name or '')} "
            f"Number={quoteattr(it.number or '')} "
            f"Volume={quoteattr(str(it.series_year or it.cover_year or ''))} "
            f"Year={quoteattr(str(it.cover_year or ''))}"
        )
        if it.cv_series_id and it.cv_issue_id:
            db = f'<Database Name="cv" Series="{it.cv_series_id}" Issue="{it.cv_issue_id}" />'
            books.append(f"<Book {attrs}>{db}</Book>")
        else:
            books.append(f"<Book {attrs} />")

    body = "\n".join(books)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ReadingList xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
        f"<Name>{_escape(name)}</Name>\n"
        f"<NumIssues>{len(items)}</NumIssues>\n"
        f"<Books>\n{body}\n</Books>\n"
        "<Matchers />\n"
        "</ReadingList>\n"
    )


def _escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
