"""Deterministic answers for the fixed competition questions (Q1-Q12).

Every figure comes from the extracted bill (Level 1 + validated history) and is computed in Python.
A handler returns None when the bill data is not enough; that question then goes to the LLM.
Questions are matched by their wording, not their IDs, so a reordered template still works.
"""
from __future__ import annotations

import re
from statistics import mean
from typing import Callable

from app.services.metrics import _prev_month, _window, month_label
from app.services.normalize import to_month

Answer = str | None


# ---------------------------------------------------------------- formatting
def num(v: float) -> str:
    v = round(v, 2)
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"


def pkr(v: float) -> str:
    return f"PKR {num(v)}"


def units_txt(v: float) -> str:
    return f"{num(v)} units"


def pct(part: float, whole: float) -> str:
    return f"{abs(part) / whole * 100:.1f}%" if whole else "n/a"


def list_months(rows: list[dict]) -> str:
    items = [f"{month_label(r['month'])} ({units_txt(r['units'])})" for r in rows]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _next_month(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y + (m == 12):04d}-{m % 12 + 1:02d}"


class Ctx:
    """Everything a handler needs, computed once per bill."""

    def __init__(self, l1: dict, extras: dict):
        self.l1, self.extras = l1, extras
        self.units, self.cb = l1.get("units_consumed"), l1.get("current_bill")
        self.bm = l1.get("bill_month")
        self.window, self.previous = _window(l1, extras)  # 12-month window incl. current, previous months
        self.history_ok = bool(self.window) and len(self.window) >= 6
        self.text = " ".join(str(t) for t in extras.get("ocr_text", []))

    @property
    def span(self) -> str:
        return f"{month_label(self.window[0]['month'])} to {month_label(self.window[-1]['month'])}"


# ---------------------------------------------------------------- Q1 net metering
NET_WORDS = ("net metering", "netmetering", "export", "import energy", "solar")


def q_net_metering(c: Ctx) -> Answer:
    text = c.text.lower()
    found = [w for w in NET_WORDS if w in text]
    negative = [r for r in c.window if r.get("units") is not None and r["units"] < 0]
    if found or negative:
        why = []
        if found:
            why.append("the bill prints " + ", ".join(f"'{w}'" for w in found))
        if negative:
            why.append("the usage history has negative units (more exported than imported), e.g. "
                       + list_months(negative[:2]))
        return "This looks like a net-metered (solar) connection: " + " and ".join(why) + "."
    return ("This appears to be a standard connection, not net-metered: the bill has no net-metering, "
            "import/export or solar wording, it shows a single consumption figure, and the usage history "
            "has no negative (exported) units.")


# ---------------------------------------------------------------- Q2 highest month
def q_highest(c: Ctx) -> Answer:
    if not c.history_ok:
        return None
    top = max(r["units"] for r in c.window)
    tops = [r for r in c.window if r["units"] == top]
    msg = (f"Over the {len(c.window)} months shown ({c.span}, including this bill), the highest consumption "
           f"was in {list_months(tops)}.")
    if c.bm and any(r["month"] == c.bm for r in tops) and c.previous:
        top_prev = max(c.previous, key=lambda r: r["units"])
        msg += f" Excluding the current month, the highest was {list_months([top_prev])}."
    return msg


# ---------------------------------------------------------------- Q3 fuel adjustment
MONTH_RE = re.compile(r"\b(?:FCA|FPA)\b\W*([A-Za-z]{3})[\s\-']*(\d{2,4})", re.I)


def q_fuel(c: Ctx) -> Answer:
    lines = [x for x in c.extras.get("charge_lines", []) if isinstance(x, dict)
             and re.search(r"fca|fpa|fuel", str(x.get("label", "")), re.I)]
    if not lines:
        lines = [{"label": "FPA", "amount": ch["amount"]} for ch in c.l1.get("charges", []) if ch["type"] == "fpa"]
    if not lines:
        return "The bill does not show a fuel price adjustment (FPA/FCA) charge this month."
    total = sum(x["amount"] for x in lines)
    month = None
    for source in [str(x.get("label", "")) for x in lines] + [c.text]:
        m = MONTH_RE.search(source)
        ym = to_month(f"{m.group(1)} {m.group(2)}") if m else None
        if ym:
            month = month_label(ym)
            break
    if month is None:
        return None  # the month is only given in the Urdu note: let the LLM read it from the image
    detail = ""
    if len(lines) == 1 and lines[0].get("units") and lines[0].get("rate"):
        detail = f", calculated on {units_txt(lines[0]['units'])} at PKR {lines[0]['rate']} per unit"
    return f"You are charged a fuel adjustment of {pkr(total)} for {month}{detail}."


# ---------------------------------------------------------------- Q4 late payment
DATE_RE = re.compile(r"(Till|Upto|After)\s*:?\s*(\d{1,2}[-/][A-Za-z0-9]{2,3}[-/]\d{2,4}(?:\s*-\s*\d{1,2}[-/][A-Za-z]{3}[-/]\d{2,4})?)", re.I)


def q_late(c: Ctx) -> Answer:
    within, after = c.l1.get("payable_within_due_date"), c.l1.get("payable_after_due_date")
    due = c.l1.get("due_date")
    if within is not None and within <= 0 and after is None:
        return (f"No late surcharge applies: the amount payable is {pkr(within)} (a credit), and the bill says "
                "nothing is to be paid after the due date.")
    amounts = sorted({a["amount"] for a in c.extras.get("late_payment_amounts", [])
                      if isinstance(a, dict) and isinstance(a.get("amount"), (int, float))}
                     | ({after} if after is not None else set()))
    steps = [a for a in amounts if within is not None and a > within]
    if within is None or not steps:
        return None
    due_txt = f" (due date {due})" if due else ""
    if len(steps) == 1:
        return (f"If you pay after the due date{due_txt}, a late payment surcharge of {pkr(steps[0] - within)} "
                f"is added, so you would pay {pkr(steps[0])} instead of {pkr(within)}.")
    parts = [f"{pkr(s - within)} (total {pkr(s)})" for s in steps]
    return (f"If you pay after the due date{due_txt}, the bill shows {len(steps)} late-payment stages: the surcharge "
            f"is first {parts[0]}, and later {' then '.join(parts[1:])}, compared with {pkr(within)} if paid on time.")


# ---------------------------------------------------------------- Q5 same month last year
def q_same_month(c: Ctx) -> Answer:
    if not (c.bm and c.units is not None):
        return None
    target = _prev_month(c.bm, 12)
    same = next((r for r in c.window + c.previous if r["month"] == target), None)
    if same is None:
        if not c.history_ok:
            return None
        return (f"The bill's history does not include {month_label(target)}, so a same-month comparison with last "
                f"year is not possible; this month you used {units_txt(c.units)}.")
    diff = c.units - same["units"]
    if diff == 0:
        return (f"No, it is the same: {units_txt(c.units)} in {month_label(c.bm)} and in {month_label(target)}.")
    word = "Yes" if diff > 0 else "No"
    more = "more" if diff > 0 else "fewer"
    return (f"{word}: you used {units_txt(c.units)} in {month_label(c.bm)} compared with {units_txt(same['units'])} "
            f"in {month_label(target)}, which is {units_txt(abs(diff))} {more} ({pct(diff, same['units'])}).")


# ---------------------------------------------------------------- Q6 average vs this month
def q_average(c: Ctx) -> Answer:
    if not (c.history_ok and c.previous and c.units is not None):
        return None
    prev = c.previous[-12:]
    avg = mean(r["units"] for r in prev)
    diff = c.units - avg
    side = "above" if diff > 0 else "below" if diff < 0 else "equal to"
    tail = f" {units_txt(abs(diff))} ({pct(diff, avg)}) {side} that average" if diff else " exactly equal to that average"
    return (f"Your average monthly consumption over the {len(prev)} months before this bill "
            f"({month_label(prev[0]['month'])} to {month_label(prev[-1]['month'])}) is {units_txt(avg)}. "
            f"This month's {units_txt(c.units)} is{tail}.")


# ---------------------------------------------------------------- Q7 summer vs winter
def q_seasons(c: Ctx) -> Answer:
    if not c.history_ok:
        return None
    summer = [r for r in c.window if r["month"][5:7] in {"06", "07", "08"}]
    winter = [r for r in c.window if r["month"][5:7] in {"12", "01", "02"}]
    if not summer or not winter:
        missing = "summer (June-August)" if not summer else "winter (December-February)"
        return f"The history shown ({c.span}) does not include the {missing} months, so this comparison is not possible."
    s_tot, w_tot = sum(r["units"] for r in summer), sum(r["units"] for r in winter)
    s_avg, w_avg = s_tot / len(summer), w_tot / len(winter)
    more = "more" if s_avg >= w_avg else "fewer"
    return (f"On average you use {units_txt(abs(s_avg - w_avg))} {more} per month in summer than in winter: "
            f"summer averages {units_txt(s_avg)} ({list_months(summer)}) and winter averages {units_txt(w_avg)} "
            f"({list_months(winter)}). In total that is {units_txt(s_tot)} versus {units_txt(w_tot)}, "
            f"a difference of {units_txt(abs(s_tot - w_tot))}.")


# ---------------------------------------------------------------- Q8 payments
def q_payments(c: Ctx) -> Answer:
    rows = [r for r in c.extras.get("usage_history", [])
            if isinstance(r, dict) and (r.get("bill_amount") is not None or r.get("payment") is not None)]
    if not rows:
        return None
    rows.sort(key=lambda r: r["month"])
    billed = [r for r in rows if (r.get("bill_amount") or 0) > 0]
    unpaid = [r for r in billed if not r.get("payment")]
    short = [r for r in billed if r.get("payment") and r["payment"] < r["bill_amount"] - 1]
    span = f"{month_label(rows[0]['month'])} to {month_label(rows[-1]['month'])}"
    if len(rows) < 6:
        intro = (f"The bill only shows payment history for {len(rows)} months ({span}), not the full 12 months. "
                 "For those months, ")
    else:
        intro = f"Looking at the {len(rows)} months with payment information ({span}), "
    if not unpaid and not short:
        return intro + "every bill was paid in full (each payment matches or exceeds the billed amount)."
    parts = []
    if unpaid:
        parts.append("no payment is shown for " + ", ".join(
            f"{month_label(r['month'])} (billed {pkr(r['bill_amount'])})" for r in unpaid))
    if short:
        parts.append("a partial payment was made for " + ", ".join(
            f"{month_label(r['month'])} (paid {pkr(r['payment'])} of {pkr(r['bill_amount'])})" for r in short))
    return intro + "not every bill was paid: " + "; ".join(parts) + "."


# ---------------------------------------------------------------- Q9 last 3 months
def q_trend(c: Ctx) -> Answer:
    if not c.history_ok or len(c.window) < 3:
        return None
    a, b, d = c.window[-3:]
    seq = f"{list_months([a])}, {list_months([b])} and {list_months([d])}"
    if a["units"] < b["units"] < d["units"]:
        return f"Your consumption has risen steadily over the last 3 months: {seq}."
    if a["units"] > b["units"] > d["units"]:
        return f"Your consumption has fallen steadily over the last 3 months: {seq}."
    first = "fell" if b["units"] < a["units"] else "rose" if b["units"] > a["units"] else "stayed the same"
    second = "fell" if d["units"] < b["units"] else "rose" if d["units"] > b["units"] else "stayed the same"
    net = d["units"] - a["units"]
    overall = (f"overall {'up' if net > 0 else 'down'} by {units_txt(abs(net))}" if net else "overall unchanged")
    return f"Your consumption has been mixed over the last 3 months: it {first} and then {second} ({seq}), {overall}."


# ---------------------------------------------------------------- Q10 next month
def q_next_month(c: Ctx) -> Answer:
    if not (c.history_ok and c.bm):
        return None
    nxt = _next_month(c.bm)
    same = next((r for r in c.window + c.previous if r["month"] == _prev_month(nxt, 12)), None)
    avg = mean(r["units"] for r in c.window)
    if same:
        return (f"Estimate: about {units_txt(same['units'])} in {month_label(nxt)}. Method: the same month last year "
                f"({month_label(same['month'])}) used {units_txt(same['units'])}, which reflects the seasonal pattern; "
                f"for comparison, the average of the {len(c.window)} months shown ({c.span}) is {units_txt(avg)}.")
    return (f"Estimate: about {units_txt(avg)} in {month_label(nxt)}. Method: the average of the {len(c.window)} "
            f"months shown ({c.span}); the same month last year is not shown on the bill.")


# ---------------------------------------------------------------- Q11 set aside per month
def q_set_aside(c: Ctx) -> Answer:
    if not c.history_ok:
        return None
    billed = [r for r in c.window if isinstance(r.get("bill_amount"), (int, float)) and r["bill_amount"] > 0]
    if len(billed) >= 6:
        total = sum(r["bill_amount"] for r in billed)
        return (f"Estimate: set aside about {pkr(total / len(billed))} per month. Method: the {len(billed)} billed "
                f"amounts shown ({month_label(billed[0]['month'])} to {month_label(billed[-1]['month'])}) total "
                f"{pkr(total)}, divided by {len(billed)}.")
    if c.units and c.cb:
        avg_units = mean(r["units"] for r in c.window)
        rate = c.cb / c.units
        return (f"Estimate: set aside about {pkr(avg_units * rate)} per month. Method: the bill does not print a "
                f"billed amount for every month, so the average use of {units_txt(avg_units)} ({c.span}) is "
                f"multiplied by this bill's average cost of PKR {rate:.2f} per unit ({pkr(c.cb)} / "
                f"{units_txt(c.units)}). Slab rates and fixed charges vary, so treat this as approximate.")
    return None


# ---------------------------------------------------------------- Q12 same units as last month
def q_same_as_last(c: Ctx) -> Answer:
    if not (c.previous and c.units and c.cb):
        return None
    last = c.previous[-1]
    rate = c.cb / c.units
    msg = (f"Estimate: about {pkr(last['units'] * rate)}. Method: last month ({month_label(last['month'])}) you used "
           f"{units_txt(last['units'])}; multiplied by this bill's average cost of PKR {rate:.2f} per unit "
           f"({pkr(c.cb)} / {units_txt(c.units)}). Slab rates and fixed charges mean the real bill can differ.")
    if isinstance(last.get("bill_amount"), (int, float)) and last["bill_amount"] > 0:
        msg += f" For reference, the bill printed for {month_label(last['month'])} was {pkr(last['bill_amount'])}."
    return msg


# ---------------------------------------------------------------- routing
HANDLERS: list[tuple[tuple[str, ...], Callable[[Ctx], Answer]]] = [
    (("net-metered", "net metered", "solar"), q_net_metering),
    (("highest units",), q_highest),
    (("fuel adjustment", "fpa", "fca"), q_fuel),
    (("late payment", "don't pay on time", "dont pay on time"), q_late),
    (("same month last year",), q_same_month),
    (("average monthly units",), q_average),
    (("summer",), q_seasons),
    (("paying every bill", "payments for the"), q_payments),
    (("last 3 months",), q_trend),
    (("expect to consume next month", "next month"), q_next_month),
    (("set aside", "spread my electricity cost"), q_set_aside),
    (("same units as last month",), q_same_as_last),
]


def match_handler(question: str) -> Callable[[Ctx], Answer] | None:
    q = question.lower()
    for keys, handler in HANDLERS:
        if any(k in q for k in keys):
            return handler
    return None


def deterministic_answers(l1: dict, extras: dict, questions: list[tuple[str, str]]) -> dict[str, str]:
    """{question_id: answer} for every question the bill data can answer exactly; others are left out."""
    ctx = Ctx(l1, extras)
    out: dict[str, str] = {}
    for qid, question in questions:
        handler = match_handler(question)
        if handler is None:
            continue
        try:
            answer = handler(ctx)
        except Exception:  # a handler bug must not cost the bill its answers: the LLM takes over
            answer = None
        if answer:
            out[qid] = answer
    return out


BAD_PATTERNS = re.compile(r"\bwait\b|let me|let's recount|steadily (up|down)\b|[؀-ۿ]", re.I)


def answer_problems(answer: str) -> list[str]:
    """Checks applied to every answer (deterministic or LLM) before it is accepted."""
    problems = []
    if not answer or not answer.strip():
        problems.append("empty")
    elif BAD_PATTERNS.search(answer):
        problems.append("scratch work, awkward wording or non-English text")
    if re.search(r"\b\d{5}-\d{7}-\d\b", answer or ""):
        problems.append("looks like a CNIC")
    return problems
