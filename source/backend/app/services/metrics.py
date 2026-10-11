"""Deterministic calculations for Level 2 answers. The LLM explains; Python does the maths."""
from __future__ import annotations

from datetime import date
from statistics import mean, median
from typing import Any

MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December"]


def _r(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(x, nd)


def _pct(part: float | None, whole: float | None) -> float | None:
    if part is None or not whole:
        return None
    return round(100.0 * part / whole, 2)


def month_label(ym: str | None) -> str | None:
    try:
        y, m = ym.split("-")
        return f"{MONTH_NAMES[int(m) - 1]} {y}"
    except Exception:
        return ym


def _prev_month(ym: str, back: int = 1) -> str:
    y, m = map(int, ym.split("-"))
    idx = y * 12 + (m - 1) - back
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def _days_between(a: str | None, b: str | None) -> int | None:
    try:
        return (date.fromisoformat(b) - date.fromisoformat(a)).days
    except Exception:
        return None


def _history_stats(rows: list[dict], label: str) -> dict:
    units = [r["units"] for r in rows if r.get("units") is not None]
    if not units:
        return {}
    hi = max(rows, key=lambda r: r["units"] if r.get("units") is not None else float("-inf"))
    lo = min(rows, key=lambda r: r["units"] if r.get("units") is not None else float("inf"))
    stats: dict[str, Any] = {
        f"{label}_months_count": len(units),
        f"{label}_total_units": _r(sum(units)),
        f"{label}_average_units": _r(mean(units)),
        f"{label}_median_units": _r(median(units)),
        f"{label}_highest": f"{month_label(hi['month'])}: {hi['units']} units",
        f"{label}_lowest": f"{month_label(lo['month'])}: {lo['units']} units",
    }
    for threshold in (100, 200, 300, 400, 500):
        above = [f"{month_label(r['month'])} ({r['units']})" for r in rows
                 if r.get("units") is not None and r["units"] > threshold]
        stats[f"{label}_months_above_{threshold}_units"] = {"count": len(above), "months": above}
    bills = [r["bill_amount"] for r in rows if r.get("bill_amount") is not None]
    if bills:
        stats[f"{label}_average_bill_amount"] = _r(mean(bills))
        stats[f"{label}_total_bill_amount"] = _r(sum(bills))
    pays = [r["payment"] for r in rows if r.get("payment") is not None]
    if pays:
        stats[f"{label}_total_payments"] = _r(sum(pays))
    return stats


def compute_metrics(l1: dict, extras: dict) -> dict:
    m: dict[str, Any] = {}
    units = l1.get("units_consumed")
    cb = l1.get("current_bill")
    tc, tt = l1.get("total_charges"), l1.get("total_taxes")
    within, after = l1.get("payable_within_due_date"), l1.get("payable_after_due_date")
    charges, taxes = l1.get("charges") or [], l1.get("taxes") or []

    m["bill_month_name"] = month_label(l1.get("bill_month"))

    # ---- meter
    prev, cur = l1.get("previous_reading"), l1.get("current_reading")
    if prev is not None and cur is not None:
        m["reading_difference"] = _r(cur - prev)

    # ---- charges & taxes
    by_type: dict[str, float] = {}
    for c in charges:
        by_type[c["type"]] = by_type.get(c["type"], 0) + c["amount"]
    m["charges_by_type"] = {k: _r(v) for k, v in by_type.items()}
    m["sum_of_charge_lines"] = _r(sum(c["amount"] for c in charges)) if charges else None
    tax_by_type: dict[str, float] = {}
    for t in taxes:
        tax_by_type[t["type"]] = tax_by_type.get(t["type"], 0) + t["amount"]
    m["taxes_by_type"] = {k: _r(v) for k, v in tax_by_type.items()}
    tax_total = tt if tt is not None else (sum(t["amount"] for t in taxes) if taxes else None)
    m["tax_total_used"] = _r(tax_total)
    m["tax_share_of_current_bill_pct"] = _pct(tax_total, cb)
    m["charges_share_of_current_bill_pct"] = _pct(tc, cb)
    if tc is not None and tax_total is not None:
        m["tax_as_pct_of_charges"] = _pct(tax_total, tc)
    for typ, amt in tax_by_type.items():
        m[f"{typ}_share_of_current_bill_pct"] = _pct(amt, cb)
    for typ, amt in by_type.items():
        m[f"{typ}_share_of_current_bill_pct"] = _pct(amt, cb)
    if "subsidy" in by_type:
        m["subsidy_amount_saved"] = _r(abs(by_type["subsidy"]))
        if tc is not None:
            m["charges_before_subsidy"] = _r(tc + abs(by_type["subsidy"]))

    # ---- per-unit costs (effective, from this bill only)
    if units:
        m["cost_per_unit_current_bill"] = _r(cb / units) if cb is not None else None
        m["cost_per_unit_charges_only"] = _r(tc / units) if tc is not None else None
        if "energy" in by_type:
            m["energy_cost_per_unit"] = _r(by_type["energy"] / units)
        if within is not None:
            m["cost_per_unit_payable_within_due"] = _r(within / units)
    rate_lines = [x for x in extras.get("charge_lines", []) if isinstance(x, dict) and x.get("rate")]
    if rate_lines:
        m["printed_rates"] = [{"label": x.get("label"), "units": x.get("units"), "rate": x.get("rate"),
                               "amount": x.get("amount")} for x in rate_lines]

    # ---- late payment
    if within is not None and after is not None:
        m["late_payment_surcharge_amount"] = _r(after - within)
        m["late_payment_surcharge_pct_of_payable"] = _pct(after - within, within)
        m["savings_by_paying_on_time"] = _r(after - within)
    lates = [x for x in extras.get("late_payment_amounts", []) if isinstance(x, dict)]
    if lates:
        m["all_late_payment_amounts"] = lates

    # ---- arrears
    arrears = l1.get("arrears")
    if arrears is not None:
        m["arrears_status"] = "credit (customer overpaid / balance in customer's favour)" if arrears < 0 \
            else ("no previous dues" if arrears == 0 else "unpaid previous dues carried forward")
        if cb is not None:
            m["current_bill_plus_arrears"] = _r(cb + arrears)

    # ---- dates
    m["days_issue_to_due"] = _days_between(l1.get("issue_date"), l1.get("due_date"))
    m["days_reading_to_due"] = _days_between(l1.get("reading_date"), l1.get("due_date"))
    m["days_reading_to_issue"] = _days_between(l1.get("reading_date"), l1.get("issue_date"))
    if l1.get("due_date"):
        try:
            m["due_date_weekday"] = date.fromisoformat(l1["due_date"]).strftime("%A")
        except ValueError:
            pass

    # ---- usage history
    hist = [r for r in extras.get("usage_history", []) if isinstance(r, dict) and r.get("units") is not None]
    bm = l1.get("bill_month")
    if hist:
        previous = [r for r in hist if bm is None or str(r["month"]) < bm]
        with_current = list(previous)
        if units is not None and bm:
            with_current = previous + [{"month": bm, "units": units,
                                        "bill_amount": cb, "payment": None}]
        m["history_months_printed"] = [f"{month_label(r['month'])}: {r['units']}" for r in hist]
        m.update(_history_stats(previous, "previous_months"))
        m.update(_history_stats(with_current, "including_current_month"))
        if previous and units is not None:
            avg = mean(r["units"] for r in previous)
            m["current_vs_previous_average_units_diff"] = _r(units - avg)
            m["current_vs_previous_average_pct"] = _pct(units - avg, avg)
            last = previous[-1]
            m["previous_month"] = f"{month_label(last['month'])}: {last['units']} units"
            m["change_vs_previous_month_units"] = _r(units - last["units"])
            m["change_vs_previous_month_pct"] = _pct(units - last["units"], last["units"])
            for n in (3, 6):
                if len(previous) >= n:
                    m[f"average_last_{n}_previous_months"] = _r(mean(r["units"] for r in previous[-n:]))
            m["months_with_higher_usage_than_current"] = sum(1 for r in previous if r["units"] > units)
            m["months_with_lower_usage_than_current"] = sum(1 for r in previous if r["units"] < units)
        if bm:
            same = next((r for r in hist if r["month"] == _prev_month(bm, 12)), None)
            if same and units is not None:
                m["same_month_last_year"] = f"{month_label(same['month'])}: {same['units']} units"
                m["change_vs_same_month_last_year_units"] = _r(units - same["units"])
                m["change_vs_same_month_last_year_pct"] = _pct(units - same["units"], same["units"])

    # ---- simple labelled estimates (based only on this bill's effective rate)
    if units and cb is not None and hist:
        avg_units = m.get("previous_months_average_units")
        if avg_units:
            m["estimate_bill_at_average_usage"] = {
                "value": _r(avg_units * cb / units),
                "method": f"average previous usage {avg_units} units x this bill's effective "
                          f"cost per unit PKR {_r(cb / units)} (current_bill / units_consumed)",
            }
    if units and cb is not None:
        m["estimate_bill_if_usage_reduced_10pct"] = {
            "value": _r(cb * 0.9),
            "method": "assumes the bill scales proportionally with units; real slab tariffs may differ",
        }
        m["estimate_daily_units_30_day_month"] = _r(units / 30)
    m.update(question_metrics(l1, extras))
    return {k: v for k, v in m.items() if v not in (None, {}, [])}


# ================================================================ per-question facts
NET_METER_WORDS = ("net metering", "netmetering", "net metering benefit", "netmeteringbenefit", "export",
                   "import energy", "solar")


def _fmt_rows(rows: list[dict]) -> list[str]:
    return [f"{month_label(r['month'])}: {r['units']} units" for r in rows]


def _window(l1: dict, extras: dict) -> tuple[list[dict], list[dict]]:
    """(12-month window incl. the current bill month, previous months only), oldest first."""
    hist = [r for r in extras.get("usage_history", []) if isinstance(r, dict) and r.get("units") is not None]
    bm, units = l1.get("bill_month"), l1.get("units_consumed")
    by_month = {str(r["month"]): r for r in hist}
    if bm and units is not None and bm not in by_month:
        by_month[bm] = {"month": bm, "units": units, "bill_amount": l1.get("current_bill"), "payment": None}
    rows = [by_month[k] for k in sorted(by_month)]
    previous = [r for r in rows if not bm or str(r["month"]) < bm]
    return rows[-12:], previous[-12:]


def question_metrics(l1: dict, extras: dict) -> dict:
    """Facts for the 12 test questions. Every count/average/estimate here is exact Python arithmetic."""
    q: dict = {}
    units, cb = l1.get("units_consumed"), l1.get("current_bill")
    within, after = l1.get("payable_within_due_date"), l1.get("payable_after_due_date")
    bm = l1.get("bill_month")
    window, previous = _window(l1, extras)

    # Q1 net metering signals (printed evidence only)
    text = " ".join(str(t) for t in extras.get("ocr_text", [])).lower()
    signals = [w for w in NET_METER_WORDS if w in text]
    if any(r.get("units") is not None and r["units"] < 0 for r in window):
        signals.append("negative units in the usage history (exported more than imported)")
    q["q1_net_metering_evidence"] = signals or [
        "none: no 'net metering', import/export or solar wording on the bill and no negative units in the history"]
    q["q1_note"] = ("Several meter rows can simply be peak/off-peak registers, and a few rupees of credit is rounding; "
                    "neither proves net metering. Decide only from the evidence listed above.")

    # Q2 highest month
    if window:
        top = max(window, key=lambda r: r["units"])
        q["q2_window_used"] = f"{month_label(window[0]['month'])} to {month_label(window[-1]['month'])} ({len(window)} months)"
        q["q2_highest_month_incl_current"] = f"{month_label(top['month'])}: {top['units']} units"
    if previous:
        top_prev = max(previous, key=lambda r: r["units"])
        q["q2_highest_month_previous_only"] = f"{month_label(top_prev['month'])}: {top_prev['units']} units"

    # Q3 fuel adjustment
    fpa = [c for c in extras.get("charge_lines", []) if isinstance(c, dict)
           and any(k in str(c.get("label", "")).lower() for k in ("fpa", "fca", "fuel"))]
    if not fpa:
        fpa = [{"label": "fpa", "amount": c["amount"]} for c in l1.get("charges", []) if c["type"] == "fpa"]
    q["q3_fuel_adjustment_lines"] = [
        {"label": c.get("label"), "amount": c.get("amount"), "units": c.get("units"), "rate": c.get("rate")}
        for c in fpa] or "no FPA/FCA line found in the extracted charges"
    q["q3_fuel_adjustment_total"] = _r(sum(c.get("amount") or 0 for c in fpa)) if fpa else None

    # Q4 late payment surcharges
    lates = sorted({a["amount"] for a in extras.get("late_payment_amounts", [])
                    if isinstance(a, dict) and isinstance(a.get("amount"), (int, float))} | ({after} if after else set()))
    if within is not None and lates:
        q["q4_late_payment_steps"] = [{"payable": a, "surcharge_added": _r(a - within)} for a in lates if a > within]
    lps_lines = [b for b in extras.get("balance_lines", []) if "surcharge" in str(b.get("label", "")).lower()]
    if lps_lines:
        q["q4_surcharge_lines_printed"] = lps_lines

    # Q5 same month last year
    if bm and units is not None:
        same = next((r for r in window + previous if r["month"] == _prev_month(bm, 12)), None)
        if same:
            q["q5_same_month_last_year"] = {"month": month_label(same["month"]), "units": same["units"],
                                            "this_month": units, "difference": _r(units - same["units"]),
                                            "pct": _pct(units - same["units"], same["units"])}
        else:
            q["q5_same_month_last_year"] = f"{month_label(_prev_month(bm, 12))} is not shown in the history"

    # Q6 average vs this month
    if previous and units is not None:
        avg_prev = mean(r["units"] for r in previous)
        q["q6_average_previous_months"] = {"months": len(previous), "average": _r(avg_prev),
                                           "this_month": units, "difference": _r(units - avg_prev),
                                           "pct": _pct(units - avg_prev, avg_prev)}
    if window:
        q["q6_average_incl_current"] = {"months": len(window), "average": _r(mean(r["units"] for r in window))}

    # Q7 summer (Jun-Aug) vs winter (Dec-Feb)
    summer = [r for r in window if str(r["month"])[5:7] in {"06", "07", "08"}]
    winter = [r for r in window if str(r["month"])[5:7] in {"12", "01", "02"}]
    if summer and winter:
        s_avg, w_avg = mean(r["units"] for r in summer), mean(r["units"] for r in winter)
        q["q7_summer_vs_winter"] = {
            "summer_months": _fmt_rows(summer), "summer_total": _r(sum(r["units"] for r in summer)),
            "summer_average": _r(s_avg),
            "winter_months": _fmt_rows(winter), "winter_total": _r(sum(r["units"] for r in winter)),
            "winter_average": _r(w_avg),
            "difference_of_averages": _r(s_avg - w_avg),
            "difference_of_totals": _r(sum(r["units"] for r in summer) - sum(r["units"] for r in winter)),
        }
    else:
        q["q7_summer_vs_winter"] = "summer or winter months are missing from the history shown"

    # Q8 payments
    paid_rows = [r for r in extras.get("usage_history", []) if isinstance(r, dict)
                 and (r.get("bill_amount") is not None or r.get("payment") is not None)]
    if paid_rows:
        unpaid = [month_label(r["month"]) for r in paid_rows
                  if (r.get("bill_amount") or 0) > 0 and not r.get("payment")]
        short = [f"{month_label(r['month'])} (billed {r['bill_amount']}, paid {r['payment']})" for r in paid_rows
                 if r.get("payment") and r.get("bill_amount") and r["payment"] < r["bill_amount"] - 1]
        q["q8_payments"] = {"months_with_bill_or_payment_shown": len(paid_rows),
                            "months_billed_but_no_payment_shown": unpaid, "months_paid_less_than_billed": short}
    else:
        q["q8_payments"] = "the bill does not show a payment history"

    # Q9 last 3 months
    if len(window) >= 3:
        last3 = window[-3:]
        vals = [r["units"] for r in last3]
        trend = ("steadily up" if vals[0] < vals[1] < vals[2] else "steadily down" if vals[0] > vals[1] > vals[2]
                 else f"mixed: {'down' if vals[1] < vals[0] else 'up'} then {'down' if vals[2] < vals[1] else 'up'}")
        q["q9_last_3_months"] = {"months": _fmt_rows(last3), "trend": trend,
                                 "change_first_to_last": _r(vals[-1] - vals[0])}

    # Q10 next month estimate
    if bm and window:
        nxt = f"{int(bm[:4]) + (bm[5:7] == '12'):04d}-{int(bm[5:7]) % 12 + 1:02d}"
        same = next((r for r in window + previous if r["month"] == _prev_month(nxt, 12)), None)
        q["q10_next_month_estimates"] = {
            "next_month": month_label(nxt),
            "same_month_last_year": f"{month_label(same['month'])}: {same['units']} units" if same else "not shown",
            "average_of_window": _r(mean(r["units"] for r in window)),
            "average_last_3_months": _r(mean(r["units"] for r in window[-3:])),
        }

    # Q11 even monthly set-aside
    amounts = [r["bill_amount"] for r in window if isinstance(r.get("bill_amount"), (int, float)) and r["bill_amount"] > 0]
    if len(amounts) >= 6:
        q["q11_monthly_set_aside"] = {"value": _r(mean(amounts)), "method": f"average of {len(amounts)} billed amounts shown",
                                      "yearly_total": _r(sum(amounts))}
    elif window and units and cb:
        avg_u = mean(r["units"] for r in window)
        q["q11_monthly_set_aside"] = {"value": _r(avg_u * cb / units), "method": (
            f"estimate: average {round(avg_u, 1)} units x this bill's effective rate PKR {_r(cb / units)}/unit "
            "(billed amounts are not shown for every month)")}

    # Q12 same units as last month
    if previous and units and cb:
        last = previous[-1]
        rate = cb / units
        q["q12_bill_at_last_month_units"] = {
            "last_month": f"{month_label(last['month'])}: {last['units']} units",
            "estimate": _r(last["units"] * rate),
            "method": f"{last['units']} units x PKR {_r(rate)}/unit (this bill's current_bill / units); "
                      "slab tariffs and fixed charges make this approximate",
            "last_month_bill_printed": last.get("bill_amount"),
        }
    return q
