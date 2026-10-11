"""Deterministic clean-up of model output into the exact Level 1 schema, plus sanity checks."""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

LEVEL1_FIELDS = [
    "provider", "tariff", "sanctioned_load_kw",
    "bill_month", "reading_date", "issue_date", "due_date",
    "previous_reading", "current_reading", "units_consumed",
    "charges", "total_charges", "taxes", "total_taxes",
    "current_bill", "arrears", "payable_within_due_date", "payable_after_due_date",
]
NUMERIC_FIELDS = {
    "sanctioned_load_kw", "previous_reading", "current_reading", "units_consumed",
    "total_charges", "total_taxes", "current_bill", "arrears",
    "payable_within_due_date", "payable_after_due_date",
}
DATE_FIELDS = {"reading_date", "issue_date", "due_date"}
CHARGE_TYPES = {"energy", "fixed", "fpa", "quarterly_adjustment", "surcharge", "meter_rent", "subsidy", "other"}
TAX_TYPES = {"gst", "electricity_duty", "income_tax", "municipal_tax", "other_tax"}
PROVIDER_PREFIX = {"KESC": "KE", "KE": "KE", "LESCO": "LESCO", "IESCO": "IESCO"}

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}

CHARGE_ALIASES = {
    "fca": "fpa", "fuel": "fpa", "fuel_price_adjustment": "fpa", "qta": "quarterly_adjustment",
    "fixed_charges": "fixed", "energy_charges": "energy", "variable": "energy", "rent": "meter_rent",
    "service_rent": "meter_rent", "relief": "subsidy",
}
TAX_ALIASES = {
    "sales_tax": "gst", "ed": "electricity_duty", "duty": "electricity_duty", "it": "income_tax",
    "muct": "municipal_tax", "kmc": "municipal_tax", "tv_fee": "other_tax", "further_tax": "other_tax",
    "extra_tax": "other_tax",
}


# ----------------------------------------------------------------- primitives
def provider_from_bill_id(bill_id: str) -> str | None:
    prefix = re.split(r"[_\-\s]", bill_id.upper(), maxsplit=1)[0]
    return PROVIDER_PREFIX.get(prefix)


def to_number(value: Any) -> float | int | None:
    """'Rs. 19,964' -> 19964, '38209CR' -> -38209, '(120)' -> -120, '' -> None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        num = float(value)
    else:
        text = str(value).strip()
        if not text or text.lower() in {"null", "none", "n/a", "na", "-", "--", "nil"}:
            return None
        cleaned = re.sub(r"(?i)rs\.?|pkr|kwh|kw|units?", "", text).strip()
        negative = bool(re.search(r"cr\.?$", cleaned, re.I)) or cleaned.startswith(("(", "-", "−")) \
            or cleaned.endswith("-")
        cleaned = re.sub(r"(?i)cr", "", cleaned)
        cleaned = re.sub(r"[^\d.]", "", cleaned)
        if cleaned.count(".") > 1:
            head, _, tail = cleaned.rpartition(".")
            cleaned = head.replace(".", "") + "." + tail
        if not cleaned or cleaned == ".":
            return None
        try:
            num = float(cleaned)
        except ValueError:
            return None
        if negative:
            num = -abs(num)
    if num != num:  # NaN
        return None
    return int(num) if num.is_integer() else round(num, 4)


def _year(y: str) -> int:
    yi = int(y)
    return 2000 + yi if yi < 100 else yi


def to_date(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"null", "none", "n/a"}:
        return None
    text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text, flags=re.I)
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
    else:
        m = re.fullmatch(r"(\d{1,2})[\s\-/.]*([A-Za-z]{3,9})[\s\-/.,]*(\d{2,4})", text)
        if m:
            d, mo, y = int(m[1]), MONTHS.get(m[2][:3].lower()), _year(m[3])
        else:
            m = re.fullmatch(r"(\d{1,2})[\-/.](\d{1,2})[\-/.](\d{2,4})", text)
            if not m:
                return None
            d, mo, y = int(m[1]), int(m[2]), _year(m[3])
    try:
        return date(y, mo, d).isoformat() if mo else None
    except (ValueError, TypeError):
        return None


def to_month(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"null", "none"}:
        return None
    m = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-\d{1,2})?", text)
    if m and 1 <= int(m[2]) <= 12:
        return f"{int(m[1]):04d}-{int(m[2]):02d}"
    m = re.fullmatch(r"([A-Za-z]{3,9})[\s\-/.,']*(\d{2,4})", text)
    if m and m[1][:3].lower() in MONTHS:
        return f"{_year(m[2]):04d}-{MONTHS[m[1][:3].lower()]:02d}"
    m = re.fullmatch(r"(\d{1,2})[\-/](\d{2,4})", text)
    if m and 1 <= int(m[1]) <= 12:
        return f"{_year(m[2]):04d}-{int(m[1]):02d}"
    return None


KEYWORDS = [  # (substring, type) checked in order for free-text labels
    ("edonfpa", "electricity_duty"), ("gstonfpa", "gst"),
    ("quarterly", "quarterly_adjustment"), ("qta", "quarterly_adjustment"), ("fuel", "fpa"),
    ("fca", "fpa"), ("fpa", "fpa"), ("subsid", "subsidy"), ("relief", "subsidy"), ("rent", "meter_rent"),
    ("surcharge", "surcharge"), ("fixed", "fixed"), ("variable", "energy"), ("energy", "energy"),
    ("electricity_charge", "energy"), ("cost_of_electricity", "energy"),
    ("sales", "gst"), ("gst", "gst"), ("duty", "electricity_duty"), ("income", "income_tax"),
    ("muct", "municipal_tax"), ("kmc", "municipal_tax"), ("municipal", "municipal_tax"),
]


def _clean_type(raw: Any, allowed: set[str], aliases: dict[str, str], fallback: str) -> str:
    key = re.sub(r"[^a-z]+", "_", str(raw or "").lower()).strip("_")
    if key in allowed:
        return key
    if key in aliases:
        return aliases[key]
    for word, typ in KEYWORDS:
        if word in key and typ in allowed:
            return typ
    return fallback


def _clean_lines(items: Any, allowed: set[str], aliases: dict[str, str], fallback: str) -> list[dict]:
    out = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        amount = to_number(item.get("amount"))
        if amount is None or amount == 0:
            continue  # rule: skip blank lines and lines printed as 0
        typ = _clean_type(item.get("type"), allowed, aliases, fallback)
        if typ == "subsidy":
            amount = -abs(amount)
        out.append({"type": typ, "amount": amount})
    return out


# ----------------------------------------------------------------- level 1
def normalize_level1(raw: dict | None, bill_id: str) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {}
    for field in LEVEL1_FIELDS:
        value = raw.get(field)
        if field == "provider":
            out[field] = provider_from_bill_id(bill_id) or (
                str(value).upper().replace("K-ELECTRIC", "KE") if value else None)
        elif field == "tariff":
            out[field] = str(value).strip() if value not in (None, "") else None
        elif field == "bill_month":
            out[field] = to_month(value)
        elif field in DATE_FIELDS:
            out[field] = to_date(value)
        elif field == "charges":
            out[field] = _clean_lines(value, CHARGE_TYPES, CHARGE_ALIASES, "other")
        elif field == "taxes":
            out[field] = _clean_lines(value, TAX_TYPES, TAX_ALIASES, "other_tax")
        elif field == "payable_after_due_date" and isinstance(value, list):
            nums = [n for n in (to_number(v) for v in value) if n is not None]
            out[field] = max(nums) if nums else None
        else:
            out[field] = to_number(value)
    return out


def empty_level1(bill_id: str) -> dict:
    return normalize_level1({}, bill_id)


def normalize_extras(raw: dict | None) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    history = []
    for row in raw.get("usage_history") or []:
        if isinstance(row, dict):
            history.append({
                "month": to_month(row.get("month")) or row.get("month"),
                "units": to_number(row.get("units")),
                "bill_amount": to_number(row.get("bill_amount")),
                "payment": to_number(row.get("payment")),
            })
    history.sort(key=lambda r: str(r["month"]))
    extras = dict(raw)
    extras["usage_history"] = history
    for key in ("charge_lines", "tax_lines", "late_payment_amounts", "printed_percentages",
                "meter_rows", "payment_history", "balance_lines", "other_facts"):
        if not isinstance(extras.get(key), list):
            extras[key] = []
    return extras


def resolve_meter_readings(l1: dict, extras: dict) -> dict:
    """Pick previous/current readings deterministically from the printed meter rows.

    One row  -> use it (fills gaps the model left).
    Many rows (peak/off-peak, net metering) -> use the single row whose units match the
    billed units; if none or several match, readings stay null rather than guessing.
    """
    rows = []
    for r in extras.get("meter_rows") or []:
        if not isinstance(r, dict):
            continue
        prev, cur = to_number(r.get("previous")), to_number(r.get("present"))
        if prev is None and cur is None:
            continue
        rows.append({"previous": prev, "present": cur, "units": to_number(r.get("units"))})
    if not rows:
        return l1
    if len(rows) == 1:
        row = rows[0]
        if l1.get("previous_reading") is None:
            l1["previous_reading"] = row["previous"]
        if l1.get("current_reading") is None:
            l1["current_reading"] = row["present"]
        return l1
    units = l1.get("units_consumed")
    matches = [r for r in rows if units is not None and r["units"] is not None and abs(r["units"] - units) <= 1]
    if len(matches) == 1:
        l1["previous_reading"], l1["current_reading"] = matches[0]["previous"], matches[0]["present"]
    else:
        l1["previous_reading"] = l1["current_reading"] = None
    return l1


def repair_credit_signs(l1: dict) -> dict:
    """Restore a dropped 'CR' sign when the printed bill arithmetic proves the amount is a credit.

    e.g. current_bill 20726 + arrears -75496 = -54770, but the model returned payable 54770.
    """
    cb, arrears = l1.get("current_bill"), l1.get("arrears")
    if cb is None or arrears is None:
        return l1
    expected = cb + arrears
    if expected >= 0:
        return l1
    for field in ("payable_within_due_date", "payable_after_due_date"):
        value = l1.get(field)
        if value is not None and value > 0 and _close(value, -expected, 3):
            l1[field] = -value
    return l1


# ----------------------------------------------------------------- checks
def _close(a: float, b: float, tol: float = 2.0) -> bool:
    return abs(a - b) <= tol


def consistency_issues(l1: dict) -> list[str]:
    """Arithmetic relationships that normally hold on a bill. Failures trigger a re-read."""
    issues = []
    prev, cur, units = l1.get("previous_reading"), l1.get("current_reading"), l1.get("units_consumed")
    if None not in (prev, cur, units) and not _close(cur - prev, units, 1) and units > 0:
        issues.append(f"current_reading - previous_reading = {cur - prev} but units_consumed = {units} "
                      "(may be fine for multiplier/net-metering meters)")
    if l1.get("charges") and l1.get("total_charges") is not None:
        s = sum(c["amount"] for c in l1["charges"])
        s_no_fpa = sum(c["amount"] for c in l1["charges"] if c["type"] != "fpa")  # IESCO bills FPA on top
        if not (_close(s, l1["total_charges"]) or _close(s_no_fpa, l1["total_charges"])):
            issues.append(f"sum of charges = {s:.2f} but total_charges = {l1['total_charges']}")
    if l1.get("taxes") and l1.get("total_taxes") is not None:
        s = sum(t["amount"] for t in l1["taxes"])
        if not _close(s, l1["total_taxes"]):
            issues.append(f"sum of taxes = {s:.2f} but total_taxes = {l1['total_taxes']}")
    tc, tt, cb = l1.get("total_charges"), l1.get("total_taxes"), l1.get("current_bill")
    if None not in (tc, tt, cb) and not _close(tc + tt, cb, 3):
        issues.append(f"total_charges + total_taxes = {tc + tt:.2f} but current_bill = {cb}")
    within, after = l1.get("payable_within_due_date"), l1.get("payable_after_due_date")
    if within is not None and after is not None and after < within:
        issues.append(f"payable_after_due_date ({after}) is less than payable_within_due_date ({within})")
    if cb is not None and within is not None and within != 0:  # 0 = net-metering credit, nothing to pay
        arr = l1.get("arrears") or 0
        fpa = sum(c["amount"] for c in l1.get("charges") or [] if c["type"] == "fpa")
        if not (_close(cb + arr, within, 3) or _close(cb + arr + fpa, within, 3)):  # IESCO bills FPA on top
            issues.append(f"current_bill + arrears = {cb + arr:.2f} but payable_within_due_date = {within}")
    dates = [l1.get(k) for k in ("reading_date", "issue_date", "due_date")]
    if all(dates):
        r, i, d = (datetime.fromisoformat(x) for x in dates)
        if not (r <= i <= d):
            issues.append(f"dates out of order: reading {dates[0]}, issue {dates[1]}, due {dates[2]}")
    for field in ("provider", "bill_month", "due_date", "units_consumed", "current_bill",
                  "payable_within_due_date"):
        if l1.get(field) is None:
            issues.append(f"{field} is null - check whether it is printed")
    return issues
