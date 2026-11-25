from datetime import date
import re

CREATED = re.compile(r"^created:[ \t]*(\S+)", re.MULTILINE)
DRAFT_MARKER = re.compile(
    r"^x-draft:[ \t]*(?i:true|yes)[ \t\r]*(?:#.*)?$", re.MULTILINE
)
_ISO_CALENDAR_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def is_draft(metadata):
    return DRAFT_MARKER.search(metadata) is not None


def parse_created(metadata):
    match = CREATED.search(metadata)
    if not match:
        return None
    raw = match.group(1)
    if not _ISO_CALENDAR_DATE.fullmatch(raw):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None
