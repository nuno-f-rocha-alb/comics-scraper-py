"""Guard for the decimal-issue parse/format in check_and_download_comics.py.

Decimal issues like #1.5 must NOT truncate to #1 (would overwrite issue 1's file).
Run: python downloader/test_issue_format.py
"""
import re


def format_issue(title: str) -> str:
    m = re.search(r"#(\d+(?:\.\d+)?)", title)
    issue_number = m.group(1) if m else "000"
    if "." in issue_number:
        int_part, frac = issue_number.split(".", 1)
        return f"{int(int_part):03}.{frac}"
    if issue_number.isdigit():
        return f"{int(issue_number):03}"
    return "000"


if __name__ == "__main__":
    assert format_issue("Spawn #1 (2024)") == "001"
    assert format_issue("Spawn #1.5 (2024)") == "001.5"          # not truncated to 001
    assert format_issue("Spawn #1 (2024)") != format_issue("Spawn #1.5 (2024)")  # no collision
    assert format_issue("Spawn #12") == "012"
    assert format_issue("Spawn Annual (2024)") == "000"          # no issue number
    print("ok")
