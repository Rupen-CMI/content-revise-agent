import calendar
import re
from datetime import date, timedelta

STALE_AFTER = timedelta(days=365)

_MONTHS: dict[str, int] = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sept": 9, "sep": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

_MONTH_ALT: str = "|".join(sorted(_MONTHS, key=len, reverse=True))

_YEAR: str = r"(?:199\d|20\d\d)"

LEADING_EVENT_DATE_RE: re.Pattern[str] = re.compile(
    r"^[ \t]*(?:[-*•]\s*)?(?:In|On|During|As of)\s+"
    r"(?:(?P<month>" + _MONTH_ALT + r")\.?\s+(?:(?P<day>\d{1,2})\s*,?\s*)?)?"
    r"(?:Q\s*(?P<quarter>[1-4])\s+)?"
    r"(?:(?P<qualifier>early|mid|late)-?\s*)?"
    r"(?P<year>" + _YEAR + r")"
    r"(?=,|\s|$)",
    re.IGNORECASE,
)

INLINE_EVENT_DATE_RE: re.Pattern[str] = re.compile(
    r"\b(?:(?P<month>" + _MONTH_ALT + r")\.?\s+(?:(?P<day>\d{1,2})\s*,\s*)?|Q\s*(?P<quarter>[1-4])\s+)"
    r"(?P<year>" + _YEAR + r")(?!\d)",
    re.IGNORECASE,
)

_QUARTER_END: dict[int, int] = {1: 3, 2: 6, 3: 9, 4: 12}


def event_date(match: re.Match[str]) -> date | None:
    """Convert a LEADING_EVENT_DATE_RE or INLINE_EVENT_DATE_RE match into a date."""
    gd: dict[str, str | None] = match.groupdict()
    try:
        year: int = int(gd.get("year") or "")
    except (TypeError, ValueError):
        return None
    month_name: str | None = gd.get("month")
    day: str | None = gd.get("day")
    quarter: str | None = gd.get("quarter")
    month_num: int = _MONTHS.get(month_name.lower().rstrip("."), 0) if month_name else 0
    try:
        if month_num and day:
            return date(year, month_num, int(day))
        if month_num:
            return date(year, month_num, calendar.monthrange(year, month_num)[1])
        if quarter:
            qm: int = _QUARTER_END[int(quarter)]
            return date(year, qm, calendar.monthrange(year, qm)[1])
        return date(year, 12, 31)
    except (ValueError, KeyError):
        return None


def is_stale(d: date, today: date | None = None) -> bool:
    """True if d is more than STALE_AFTER before today."""
    ref: date = today or date.today()
    return d < ref - STALE_AFTER


def strip_stale_developments(text: str, today: date | None = None) -> tuple[str, list[str]]:
    """Drop lines whose leading event date is stale; return (kept text, removed lines)."""
    ref: date = today or date.today()
    kept: list[str] = []
    removed: list[str] = []
    for line in text.split("\n"):
        m: re.Match[str] | None = LEADING_EVENT_DATE_RE.match(line)
        if m is not None:
            d: date | None = event_date(m)
            if d is not None and is_stale(d, ref):
                removed.append(line)
                continue
        kept.append(line)
    return "\n".join(kept), removed
