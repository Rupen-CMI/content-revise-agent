"""The one sentence splitter the whole pipeline uses (the write backstops, the FAQ steps and the checks).

A full stop after an abbreviation or a number marker ("Inc.", "No. 36", "U.S.") is not a sentence end. A splitter that
treated it as one cut a sentence in two ("...acquired Centrolingue, that Inc." | "5000-listed Argo Translation...") and the
backstops that remove a sentence then dropped the second half, printing half a sentence. Every splitter in the code base
imports this one, so the abbreviation list lives in a single place. Pure; no model call."""
import re

# Each one is a full stop that does NOT end a sentence. "U.S." and "U.K." are listed because they are almost always part of a
# phrase ("U.S. Senators", "the U.S. market"); the cost is that a sentence that really ends on one is not split there.
ABBREVIATIONS = (
    "U.S.", "U.K.", "U.N.", "E.U.", "Inc.", "Ltd.", "Corp.", "Co.", "No.", "Nos.", "St.", "Mt.", "Ft.", "Mr.", "Mrs.", "Ms.",
    "Dr.", "Prof.", "Jr.", "Sr.", "Gen.", "Gov.", "Sen.", "Rep.", "Dept.", "Approx.", "vs.", "e.g.", "i.e.",
    "Jan.", "Feb.", "Aug.", "Sept.", "Oct.", "Nov.", "Dec.",
)

# The text between two sentences: whitespace after . ! ? unless the stop belongs to an abbreviation. A lookbehind is added
# for each abbreviation (each has a fixed width), so Python's re accepts the whole pattern.
BOUNDARY = r"(?<=[.!?])" + "".join(r"(?<!\b" + re.escape(a) + ")" for a in ABBREVIATIONS) + r"\s+"
SPLIT_RE = re.compile(BOUNDARY)


def split(text: str) -> list[str]:
    """The sentences of a line of text, as the plain `re.split` on whitespace after . ! ? would give them but without
    cutting at an abbreviation."""
    return SPLIT_RE.split(text or "")
