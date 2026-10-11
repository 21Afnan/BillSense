"""Validation of the usage/billing history the LLM transcribes from a bill image.

The LLM only reads the chart/table; every count and average is computed in Python, so the history must be
trustworthy before it is used. Rows that cannot be trusted are dropped rather than guessed.
"""
from __future__ import annotations

from app.services.normalize import to_month


def _index(ym: str) -> int:
    y, m = ym.split("-")
    return int(y) * 12 + int(m) - 1


def validate_history(rows: list[dict], level1: dict) -> tuple[list[dict], list[str]]:
    """Return (clean rows oldest-first, problems). Problems explain what was wrong; clean rows are safe to use."""
    problems: list[str] = []
    bill_month = level1.get("bill_month")
    units = level1.get("units_consumed")
    by_month: dict[str, dict] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        month = to_month(row.get("month"))
        if month is None:
            problems.append(f"unreadable month {row.get('month')!r}")
            continue
        if bill_month and month > bill_month:
            problems.append(f"{month} is after the bill month {bill_month}")
            continue
        if month in by_month:
            if by_month[month].get("units") != row.get("units"):
                problems.append(f"{month} appears twice with different units")
            continue
        by_month[month] = {"month": month, "units": row.get("units"), "bill_amount": row.get("bill_amount"),
                           "payment": row.get("payment")}

    clean = [by_month[m] for m in sorted(by_month)]
    if clean:
        gaps = [f"{a['month']}..{b['month']}" for a, b in zip(clean, clean[1:])
                if _index(b["month"]) - _index(a["month"]) != 1]
        if gaps:
            problems.append(f"missing months between {', '.join(gaps)}")
        if len(clean) < 6:
            problems.append(f"only {len(clean)} months read (a full history shows 12-13)")
    if bill_month and units is not None and bill_month in by_month:
        printed = by_month[bill_month].get("units")
        if printed is not None and abs(printed - units) > 1:
            problems.append(f"history shows {printed} units for {bill_month} but the bill says {units}")
    return clean, problems


def is_severe(problems: list[str]) -> bool:
    """Problems that make counts/averages unreliable (a short history alone is not severe)."""
    return any(not p.startswith("only ") for p in problems)
