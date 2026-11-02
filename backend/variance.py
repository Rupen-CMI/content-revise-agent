"""Variance for figures taken from an outside report.

When no CMI report fits a blog's market, the market's figures come from another publisher's report. Those exact numbers
are never printed: each is moved by 3 to 5 percent, and the audit trail keeps the original beside the shown value so an
editor can see both (the SOP panel prints it).

How the numbers stay believable:
  - size and forecast each move by their own factor, and the CAGR is recomputed from the moved pair over the same years,
    so the three figures still agree with one another;
  - category shares move relatively, keep their order (a leader stays the leader) and keep their total;
  - category amounts move with the size's factor;
  - the factors are seeded from the market name and source, so regenerating a draft gives the same shown figures;
  - a year is never moved.
Pure given its seed: no network, no model.
"""
import hashlib
import random
import re

LOW, HIGH = 0.03, 0.05

# A number is digits with proper thousands groups (a comma that merely follows it, "in 2026, the...", is not part of it)
_NUM_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d\d(?!\d)")
# Labels that look like numbers but are dates or periods: a quarter ("Q2"), a half ("H1"), a fiscal year ("FY26") and a day of a month
# ("September 18"). They stay exactly as the source wrote them; only measured amounts move.
_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec"
# an age ("aged 18 to 65+", "40 year-old") is a label of the group surveyed, not a measured amount
_AGES = (r"\bages?d?\s+(?:between\s+)?\d{1,3}\+?(?:\s*(?:to|and|-|\u2013)\s*\d{1,3}\+?)?"
         r"|\b\d{1,3}(?:\s*(?:to|-|\u2013)\s*\d{1,3})?\s*(?:years?|yrs?)[- ]old\b")
_LABEL_RE = re.compile(rf"\b[QH][1-4]\b|\bFY\s?\d{{2,4}}\b|\b(?:{_MONTHS})\.?\s+\d{{1,2}}(?!\d)|{_AGES}", re.I)


def _rng(*parts: str) -> random.Random:
    return random.Random(int(hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16], 16))


def _factor(rng: random.Random) -> float:
    return 1 + rng.choice((-1, 1)) * rng.uniform(LOW, HIGH)


def _decimals(token: str) -> int:
    return len(token.split(".")[1]) if "." in token else 0


def _format(value: float, decimals: int, comma: bool) -> str:
    text = f"{value:,.{decimals}f}" if comma else f"{value:.{decimals}f}"
    return text


def scale_token(token: str, factor: float) -> str:
    """The numeric token scaled by `factor`, written with the same decimals and thousands commas, and never equal to the
    original (a rounding that lands on the same digits is nudged by one unit of the last digit)."""
    plain = token.replace(",", "")
    decimals, comma = _decimals(plain), "," in token
    original = float(plain)
    shown = _format(original * factor, decimals, comma)
    if shown.replace(",", "") == plain:
        step = 10 ** -decimals if decimals else 1
        shown = _format(original + (step if factor >= 1 else -step), decimals, comma)
    return shown


def scale_text(text: str, factor: float) -> str:
    """Every number in `text` scaled by `factor` except years, quarters, halves, fiscal years, days of a month and ages."""
    keep = {m.span() for m in _YEAR_RE.finditer(text)} | {m.span() for m in _LABEL_RE.finditer(text)}

    def one(m: re.Match) -> str:
        if any(s <= m.start() and m.end() <= e for s, e in keep):
            return m.group(0)
        return scale_token(m.group(0), factor)

    return _NUM_RE.sub(one, text)


def _first_number(text: str) -> str | None:
    for m in _NUM_RE.finditer(text or ""):
        if not _YEAR_RE.fullmatch(m.group(0)):
            return m.group(0)
    return None


def _amount(text: str) -> float | None:
    token = _first_number(text)
    return float(token.replace(",", "")) if token else None


def _year(text: str) -> int | None:
    years = [int(y) for y in _YEAR_RE.findall(text or "")]
    return max(years) if years else None


def _record(audit: list[dict], field: str, original: str, shown: str, source: dict) -> None:
    audit.append({"field": field, "original": original, "shown": shown, "source": source.get("name", ""), "url": source.get("url", "")})


def _vary_headlines(headlines: dict, source: dict, rng: random.Random) -> tuple[list[dict], float]:
    """Move the size and forecast entries of `headlines` by their own seeded factors and recompute the CAGR from the
    moved pair over the same years, so the three figures still agree with one another (a growth rate whose pair has
    no shown amounts or no years moves by its own factor instead). Every varied entry keeps `original_value` and is
    marked `varied`. Returns (audit rows, the size factor — `vary_fact_sheet` moves value-kind category amounts by
    it) ; mutates in place. `rng` is passed in so callers share one draw sequence."""
    audit: list[dict] = []
    # Size and forecast move the SAME way (both up or both down, each by its own 3-5%): moved in opposite directions the
    # recomputed growth rate would drift far from the source's, and a rate of 13% beside a source's 11.6% looks invented.
    f_size = _factor(rng)
    f_forecast = 1 + (1 if f_size >= 1 else -1) * rng.uniform(LOW, HIGH)

    size, forecast, cagr = headlines.get("size"), headlines.get("forecast"), headlines.get("cagr")
    shown_amounts: dict[str, float] = {}
    for key, entry, factor in (("size", size, f_size), ("forecast", forecast, f_forecast)):
        if not entry:
            continue
        token = _first_number(entry["value"])
        if not token:
            continue
        entry["original_value"] = entry["value"]
        entry["value"] = entry["value"].replace(token, scale_token(token, factor), 1)
        shown_amounts[key] = _amount(entry["value"])
        _record(audit, key, entry["original_value"], entry["value"], source)
    if cagr:
        entry = cagr
        entry["original_value"] = entry["value"]
        years = (_year(size["when"]) if size else None, _year(forecast["when"]) if forecast else None)
        if all(shown_amounts.get(k) for k in ("size", "forecast")) and all(years) and years[1] > years[0]:
            rate = ((shown_amounts["forecast"] / shown_amounts["size"]) ** (1 / (years[1] - years[0])) - 1) * 100
            token = _first_number(entry["value"])
            decimals = _decimals(token) if token else 1
            new = _format(rate, decimals, False)
            if token and new == token:
                new = _format(rate + (10 ** -decimals), decimals, False)
            entry["value"] = entry["value"].replace(token, new, 1) if token else f"{new}%"
        else:
            token = _first_number(entry["value"])
            if token:
                entry["value"] = entry["value"].replace(token, scale_token(token, _factor(rng)), 1)
        _record(audit, "cagr", entry["original_value"], entry["value"], source)
    for entry in (e for e in (size, forecast, cagr) if e):
        entry["varied"] = True
    return audit, f_size


def vary_headline_entries(fs: dict, market_name: str, source: dict) -> list[dict]:
    """Move only the headline entries of `fs` that came from outside pages (`entry["external"]`) and have not been
    varied yet: size and forecast each by their own seeded 3-5% factor, the CAGR recomputed from the moved pair over
    the same years. The factors are seeded from the market and the source url, so the same call always shows the
    same figures. The original value is kept beside the shown one and the audit rows are appended to fs["variance"]
    (created when missing); a second call changes nothing — entries already marked varied are skipped. Returns the
    audit rows."""
    headlines = {k: fs[k] for k in ("size", "forecast", "cagr")
                 if fs.get(k) and fs[k].get("external") and not fs[k].get("varied")}
    if not headlines or not (source or {}).get("url"):
        return []
    audit, _ = _vary_headlines(headlines, source, _rng(market_name.lower(), source["url"]))
    fs["variance"] = list(fs.get("variance") or []) + audit
    return audit


def vary_fact_sheet(fs: dict, market_name: str) -> list[dict]:
    """Move every figure of an outside-report fact sheet in place and return the audit trail
    [{field, original, shown, source, url}]. A sheet that is not from an outside report is left alone."""
    source = fs.get("external")
    if not source:
        return []
    rng = _rng(market_name.lower(), source.get("url", ""))
    audit, f_size = _vary_headlines({k: fs.get(k) for k in ("size", "forecast", "cagr")}, source, rng)

    for seg in fs.get("segments") or []:
        figures = seg.get("figures") or {}
        shares = {n: f for n, f in figures.items() if f["kind"] == "share"}
        if shares:
            total = sum(f["amount"] for f in shares.values())
            moved = {n: f["amount"] * _factor(rng) for n, f in shares.items()}
            order = sorted(shares, key=lambda n: shares[n]["amount"], reverse=True)
            values = sorted(moved.values(), reverse=True)
            moved = dict(zip(order, values))                      # same order as the original shares
            moved_total = sum(moved.values())
            scale = total / moved_total if 95 <= total <= 105 and moved_total else 1.0
            for n in order:
                f = figures[n]
                token = _first_number(f["figure"])
                shown_amount = round(moved[n] * scale, _decimals(token) if token else 1)
                if token and shown_amount == f["amount"]:
                    shown_amount = round(shown_amount + (10 ** -(_decimals(token))), _decimals(token))
                new = f["figure"].replace(token, _format(shown_amount, _decimals(token), False), 1) if token else f["figure"]
                f["original_figure"], f["figure"], f["amount"], f["varied"] = f["figure"], new, shown_amount, True
                _record(audit, f"{seg['type']}: {n}", f["original_figure"], new, source)
        for n, f in figures.items():
            if f["kind"] != "value":
                continue
            token = _first_number(f["figure"])
            if not token:
                continue
            new = f["figure"].replace(token, scale_token(token, f_size), 1)
            f["original_figure"], f["figure"], f["varied"] = f["figure"], new, True
            f["amount"] = f["amount"] * f_size
            _record(audit, f"{seg['type']}: {n}", f["original_figure"], new, source)

    for key in ("us", "drivers", "opportunities"):
        for e in fs.get(key) or []:
            if _first_number(e["text"]):
                original = e["text"]
                e["text"] = scale_text(original, _factor(rng))
                e["original_text"] = original
                _record(audit, f"{key}: statement", original, e["text"], source)
    fs["variance"] = audit
    return audit
