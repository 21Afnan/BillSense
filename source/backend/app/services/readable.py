"""Human-friendly export, written next to the official level1.csv (which stays untouched).

level1_readable.csv  one row per bill, one clearly named column per field
Uses a UTF-8 BOM so Excel shows it correctly.
"""
from __future__ import annotations

from pathlib import Path

from app.services.csv_io import to_csv_text, write_text_file
from app.services.pipeline import BillResult

TYPE_NAMES = {
    "energy": "Energy", "fixed": "Fixed", "fpa": "Fuel adjustment (FPA)",
    "quarterly_adjustment": "Quarterly adjustment", "surcharge": "Surcharge", "meter_rent": "Meter rent",
    "subsidy": "Subsidy", "other": "Other", "gst": "Sales tax (GST)", "electricity_duty": "Electricity duty",
    "income_tax": "Income tax", "municipal_tax": "Municipal tax", "other_tax": "Other tax",
}

# (column heading, level1 field)
SUMMARY_COLUMNS = [
    ("Bill ID", None), ("Provider", "provider"), ("Tariff", "tariff"),
    ("Sanctioned Load (kW)", "sanctioned_load_kw"), ("Bill Month", "bill_month"),
    ("Reading Date", "reading_date"), ("Issue Date", "issue_date"), ("Due Date", "due_date"),
    ("Previous Reading", "previous_reading"), ("Current Reading", "current_reading"),
    ("Units Consumed", "units_consumed"), ("Charges (itemised, PKR)", "charges"),
    ("Total Charges (PKR)", "total_charges"), ("Taxes (itemised, PKR)", "taxes"),
    ("Total Taxes (PKR)", "total_taxes"), ("Current Bill (PKR)", "current_bill"), ("Arrears (PKR)", "arrears"),
    ("Payable by Due Date (PKR)", "payable_within_due_date"),
    ("Payable after Due Date (PKR)", "payable_after_due_date"),
    ("Consistency Checks", None), ("Status", None),
]


def _val(v) -> str:
    if v is None:
        return "not shown"
    if isinstance(v, float):
        return f"{v:,.2f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _items(items: list[dict]) -> str:
    if not items:
        return "none itemised"
    return " | ".join(f"{TYPE_NAMES.get(i['type'], i['type'])}: {_val(i['amount'])}" for i in items)


def summary_row(res: BillResult) -> dict:
    l1, row = res.level1, {}
    for heading, field in SUMMARY_COLUMNS:
        if field in ("charges", "taxes"):
            row[heading] = _items(l1.get(field) or [])
        elif field:
            row[heading] = _val(l1.get(field))
    row["Bill ID"] = res.bill_id
    row["Consistency Checks"] = "all passed" if not res.issues else "; ".join(res.issues)
    row["Status"] = f"error: {res.error}" if res.error else ("ok (AI re-checked)" if res.reviewed else "ok")
    return row


def write_readable(out_dir: Path, results: list[BillResult]) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = out_dir / "level1_readable.csv"
    write_text_file(summary, to_csv_text([h for h, _ in SUMMARY_COLUMNS], [summary_row(r) for r in results]),
                    encoding="utf-8-sig")
    return [summary]
