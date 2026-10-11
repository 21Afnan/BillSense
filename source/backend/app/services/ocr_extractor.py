"""Level 1 extraction with local OCR (RapidOCR / PaddleOCR models) + layout rules. No LLM involved.

Steps: prepare image -> OCR tokens with boxes -> layout-specific parser (KE or LESCO/IESCO)
       -> raw Level 1 dict (labels as charge types) -> normalize.py does the final schema clean-up.
"""
from __future__ import annotations

import hashlib
import io
import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher, get_close_matches
from functools import cached_property
from pathlib import Path
from typing import Callable

from PIL import Image, ImageOps

from app.core.cache import read_json, write_json_atomic
from app.services.normalize import to_date

log = logging.getLogger(__name__)

MIN_WIDTH = 1600  # small/phone images are upscaled before OCR; it recovers many missed digits
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
DIGIT_TO_LETTER = str.maketrans({"0": "O", "1": "I", "5": "S", "8": "B", "6": "G", "4": "A"})


# ============================================================== tokens
@dataclass
class Token:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    conf: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def h(self) -> float:
        return max(self.y1 - self.y0, 1)

    @cached_property
    def norm(self) -> str:
        return re.sub(r"[^a-z0-9]", "", self.text.lower())


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


# ============================================================== value parsers
def parse_amount(text: str | None) -> float | int | None:
    """'Rs.3,430.24' -> 3430.24, '3.777.49' -> 3777.49, '54770CR' -> -54770, '39411/2' -> 39411."""
    if not text:
        return None
    t = text.replace(" ", "")
    m = re.search(r"-?\d[\d,.]*", t)
    if not m:
        return None
    raw = m.group(0)
    negative = raw.startswith("-") or bool(re.search(r"\d\s*CR\b|\dCR", text.upper()))
    digits = raw.lstrip("-").rstrip(".,")
    seps = [i for i, c in enumerate(digits) if c in ",."]
    if seps:
        last = seps[-1]
        decimals = len(digits) - last - 1
        if decimals == 3 or (decimals > 2 and digits[last] == ","):  # thousands separators only
            digits = re.sub(r"[,.]", "", digits)
        else:
            digits = re.sub(r"[,.]", "", digits[:last]) + "." + digits[last + 1:]
    try:
        value = float(digits)
    except ValueError:
        return None
    value = -value if negative else value
    return int(value) if value.is_integer() else round(value, 2)


def _month(token: str) -> int | None:
    guess = get_close_matches(token.upper().translate(DIGIT_TO_LETTER), MONTHS, n=1, cutoff=0.6)
    return MONTHS.index(guess[0]) + 1 if guess else None


def parse_date(text: str | None) -> str | None:
    """'07-Apr-2026', '21st Apr 2026', '25SEP26', '080CT26', '16-Mar-26', '03/08/26' -> ISO date."""
    if not text:
        return None
    t = text.strip()
    m = re.search(r"(\d{1,2})(?:st|nd|rd|th)?[\s\-.]*([A-Za-z][A-Za-z0-9]{2,3}|0[A-Za-z]{2})[\s\-.,']*(\d{4}|\d{2})(?!\d)", t)
    if m:
        month = _month(m.group(2)[:3] if len(m.group(2)) == 4 and m.group(2)[3].isdigit() else m.group(2))
        if month:
            return to_date(f"{int(m.group(1))} {MONTHS[month - 1]} {m.group(3)}")
    m = re.search(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})", t)
    if m:
        return to_date(f"{m.group(1)}/{m.group(2)}/{m.group(3)}")
    return None


def parse_month(text: str | None) -> str | None:
    """'AUG2026', 'SEP26', 'Apr-26', 'BillMonth:Apr-26' -> 'YYYY-MM'."""
    if not text:
        return None
    m = re.search(r"([A-Za-z0-9]{3})[\s\-.,']*(\d{4}|\d{2})(?!\d)\s*$", text.strip())
    if not m:
        return None
    month = _month(m.group(1))
    if not month:
        return None
    year = int(m.group(2))
    year = 2000 + year if year < 100 else year
    return f"{year:04d}-{month:02d}"


TARIFF_RE = re.compile(r"^[A-E]-?\d{1,2}[A-Za-z]?(?:\(\d{2}\))?[A-Z]?$|^[A-E]\d-[A-Z]{1,2}$")


def parse_tariff(text: str | None) -> str | None:
    """Accept only text that looks like a real tariff code; OCR noise becomes None (scored better than a wrong code)."""
    if not text:
        return None
    t = re.sub(r"^(?:TARIFF|TARIF|TAR)[:.]?", "", text.strip(), flags=re.I).replace(" ", "")
    t = t.replace("O", "0") if re.search(r"\(\w{2}\)", t) else t
    t = re.sub(r"\((\w)(\w)\)", lambda m: "(" + m.group(1).replace("O", "0") + m.group(2).replace("O", "0") + ")", t)
    return t if TARIFF_RE.match(t) else None


def parse_number(text: str | None) -> float | int | None:
    if not text or re.search(r"[A-Za-z]{2,}", re.sub(r"Rs\.?|CR\b|CR$", "", text.strip())):
        return None
    return parse_amount(text)


# ============================================================== OCR document
class OcrDocument:
    def __init__(self, tokens: list[Token], width: int, height: int):
        self.tokens = sorted(tokens, key=lambda t: (t.y0, t.x0))
        self.width, self.height = width, height

    # ---- label search
    def find(self, *aliases: str, region: tuple[float, float, float, float] | None = None,
             exact: bool = False, cutoff: float = 0.78) -> list[Token]:
        """Tokens matching any alias (fuzzy, space-insensitive), best match first."""
        found: list[tuple[float, Token]] = []
        for tok in self._in(region):
            best = 0.0
            for alias in aliases:
                a = norm(alias)
                if exact:
                    score = 1.0 if tok.norm == a else SequenceMatcher(None, tok.norm, a).ratio()
                elif a in tok.norm:
                    score = 1.0
                else:
                    score = SequenceMatcher(None, tok.norm[: len(a) + 2], a).ratio()
                best = max(best, score)
            if best >= cutoff:
                found.append((best, tok))
        return [t for _, t in sorted(found, key=lambda p: (-p[0], p[1].y0))]

    def _in(self, region):
        if not region:
            return self.tokens
        x0, y0, x1, y1 = region
        return [t for t in self.tokens if x0 <= t.cx <= x1 and y0 <= t.cy <= y1]

    # ---- value search relative to a label
    def right_of(self, label: Token, parse: Callable, max_dx: float | None = None, dy: float = 0.7):
        row = [t for t in self.tokens if t is not label and t.x0 >= label.x1 - 5
               and abs(t.cy - label.cy) <= max(label.h, t.h) * dy
               and (max_dx is None or t.x0 - label.x1 <= max_dx)]
        for tok in sorted(row, key=lambda t: t.x0):
            value = parse(tok.text)
            if value is not None:
                return value, tok
        return None, None

    def below(self, label: Token, parse: Callable, max_rows: float = 4.0, slack: float = 40):
        col = [t for t in self.tokens if t is not label and t.y0 >= label.y0 + label.h * 0.5
               and t.y0 - label.y1 <= label.h * max_rows
               and t.x0 < label.x1 + slack and t.x1 > label.x0 - slack]
        for tok in sorted(col, key=lambda t: (round(t.y0 / max(label.h, 1)), abs(t.cx - label.cx))):
            value = parse(tok.text)
            if value is not None:
                return value, tok
        return None, None

    def value(self, aliases, parse, where="below", region=None, **kw):
        for label in self.find(*aliases, region=region):
            value, _ = (self.below if where == "below" else self.right_of)(label, parse, **kw)
            if value is not None:
                return value
        return None

    def rows(self, region, tol: float = 0.6) -> list[list[Token]]:
        """Group tokens in a region into visual rows (top to bottom, left to right)."""
        rows: list[list[Token]] = []
        for tok in sorted(self._in(region), key=lambda t: t.cy):
            if rows and abs(rows[-1][0].cy - tok.cy) <= max(rows[-1][0].h, tok.h) * tol:
                rows[-1].append(tok)
            else:
                rows.append([tok])
        return [sorted(r, key=lambda t: t.x0) for r in rows]


# ============================================================== OCR engine
class OcrEngine:
    def __init__(self, cache_dir: Path):
        try:
            from rapidocr_onnxruntime import RapidOCR  # local model, no network calls
        except ImportError as err:
            raise RuntimeError("OCR package missing: run  pip install -r requirements.txt") from err
        self._ocr = RapidOCR()
        self.cache_dir = cache_dir / "ocr"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def prepare(data: bytes) -> Image.Image:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
        if img.width < MIN_WIDTH:
            scale = (MIN_WIDTH + 100) / img.width
            img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
        return img

    def read(self, data: bytes) -> list[OcrDocument]:
        """OCR passes: [upscaled-or-original, original] - two views recover more fields on phone photos."""
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
        views = [self.prepare(data)] + ([img] if img.width < MIN_WIDTH else [])
        digest = hashlib.sha256(data).hexdigest()
        return [self._ocr_view(view, f"{digest}_{i}") for i, view in enumerate(views)]

    def _ocr_view(self, img: Image.Image, key: str) -> OcrDocument:
        cache = self.cache_dir / f"{key}.json"
        payload = read_json(cache)
        if not (isinstance(payload, dict) and "tokens" in payload):
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            result, _ = self._ocr(buf.getvalue())
            items = []
            for box, text, conf in result or []:
                xs, ys = [p[0] for p in box], [p[1] for p in box]
                items.append([text, min(xs), min(ys), max(xs), max(ys), float(conf)])
            payload = {"width": img.width, "height": img.height, "tokens": items}
            write_json_atomic(cache, payload)
        tokens = [Token(t, x0, y0, x1, y1, c) for t, x0, y0, x1, y1, c in payload["tokens"]]
        return OcrDocument(tokens, payload["width"], payload["height"])


# ============================================================== KE layout
def parse_ke(doc: OcrDocument) -> tuple[dict, dict]:
    W, H = doc.width, doc.height
    out: dict = {"charges": [], "taxes": []}
    ex: dict = {"charge_lines": [], "tax_lines": [], "late_payment_amounts": [], "meter_rows": [],
                "balance_lines": []}

    def inline(pattern: str):
        for tok in doc.tokens:
            m = re.search(pattern, tok.text.replace(" ", ""), re.I)
            if m:
                return m.group(1)
        return None

    out["bill_month"] = parse_month(inline(r"BillMonth:?([A-Za-z0-9]{3}-?\d{2,4})"))
    out["issue_date"] = parse_date(inline(r"IssueDate:?(\d{1,2}-[A-Za-z0-9]{3}-\d{2,4})"))
    out["reading_date"] = parse_date(inline(r"ReadingDate:?(\d{1,2}-[A-Za-z0-9]{3}-\d{2,4})"))
    big_due = next((t for t in doc.tokens if re.fullmatch(r"\d{1,2}(st|nd|rd|th)[A-Za-z]{3}\d{4}", t.text.replace(" ", ""))), None)
    out["due_date"] = parse_date(big_due.text) if big_due else doc.value(["Due Date"], parse_date)
    out["sanctioned_load_kw"] = doc.value(["Sanc Load", "S.Load"], parse_number, where="right")
    out["tariff"] = doc.value(["Tariff"], parse_tariff, where="right")
    units = inline(r"(\d[\d,.]*)units")
    out["units_consumed"] = parse_amount(units)

    # ---- meter table ("Electricity You Have Used")
    head = doc.find("Previous Reading", region=(0, 0, W * 0.55, H * 0.6))
    stop = doc.find("Month Usage History", "Usage History", region=(0, 0, W * 0.55, H))
    if head:
        y_end = stop[0].y0 if stop and stop[0].y0 > head[0].y1 else head[0].y1 + H * 0.12
        for row in doc.rows((0, head[0].y1 + 2, W * 0.55, y_end)):
            nums = [parse_amount(t.text) for t in row if parse_number(t.text) is not None]
            label = " ".join(t.text for t in row if parse_number(t.text) is None)
            if len(nums) >= 3:
                ex["meter_rows"].append({"label": label or None, "previous": nums[0], "present": nums[1],
                                         "units": nums[2], "mdi_kw": nums[3] if len(nums) > 3 else None})
    if len(ex["meter_rows"]) == 1:
        out["previous_reading"] = ex["meter_rows"][0]["previous"]
        out["current_reading"] = ex["meter_rows"][0]["present"]

    # ---- bill calculation table (right half)
    amount_hdr = next((t for t in doc.tokens if t.norm == "amount" and t.cx > W * 0.6), None)
    if amount_hdr:
        y_top = amount_hdr.y1
        state, after = "charges", []
        for row in doc.rows((W * 0.5, y_top, W, H * 0.85)):
            amount_tok = next((t for t in reversed(row) if abs(t.x1 - amount_hdr.x1) < W * 0.05
                               and parse_number(t.text) is not None), None)
            label_toks = [t for t in row if parse_number(t.text) is None]
            if not amount_tok or not label_toks:
                continue
            label = " ".join(t.text for t in label_toks)
            n, amount = norm(label), parse_amount(amount_tok.text)
            nums = [parse_amount(t.text) for t in row if t is not amount_tok and parse_number(t.text) is not None]
            if state == "charges":
                if n.startswith("electricitycharges") and "current" not in n:
                    out["total_charges"], state = amount, "taxes"
                    continue
                out["charges"].append({"type": label, "amount": amount})
                ex["charge_lines"].append({"label": label, "units": nums[0] if nums else None,
                                           "rate": nums[1] if len(nums) > 1 else None, "amount": amount})
            elif state == "taxes":
                if "taxesandduties" in n or SequenceMatcher(None, n, "taxesandduties").ratio() > 0.8:
                    out["total_taxes"], state = amount, "after"
                    continue
                out["taxes"].append({"type": label, "amount": amount})
                ex["tax_lines"].append({"label": label, "amount": amount})
            else:
                ex["balance_lines"].append({"label": label, "amount": amount})
                if "currentmonth" in n:
                    out["current_bill"] = amount
                elif n.startswith("previousdues"):
                    out["arrears"] = amount
                elif "payablewithin" in n:
                    out["payable_within_due_date"] = amount
                elif "payableafter" in n or "payablefrom" in n:
                    after.append(amount)
                    ex["late_payment_amounts"].append({"label": label, "amount": amount})
        if after:
            out["payable_after_due_date"] = max(after)

    # ---- summary boxes at the top (fallbacks)
    rs = lambda s: parse_amount(s) if s and re.search(r"rs", s, re.I) else None  # noqa: E731
    if out.get("current_bill") is None:
        out["current_bill"] = doc.value(["Current Month"], rs, max_rows=5)
    if out.get("arrears") is None:
        out["arrears"] = doc.value(["Previous Dues"], rs, max_rows=4)
    if out.get("payable_within_due_date") is None:
        out["payable_within_due_date"] = doc.value(["Amount Due"], rs, max_rows=4)
    if out.get("payable_after_due_date") is None:
        box = doc.find("Amount Payable after Due Date")
        if box:
            vals = [rs(t.text) for t in doc._in((0, box[0].y1, box[0].x1 + 40, box[0].y1 + box[0].h * 4))]
            vals = [v for v in vals if v is not None]
            if vals:
                out["payable_after_due_date"] = max(vals)
    return out, ex


# ============================================================== LESCO / IESCO layout
def parse_disco(doc: OcrDocument) -> tuple[dict, dict]:
    W, H = doc.width, doc.height
    out: dict = {"charges": [], "taxes": []}
    ex: dict = {"charge_lines": [], "tax_lines": [], "late_payment_amounts": [], "meter_rows": [],
                "balance_lines": []}

    out["bill_month"] = doc.value(["BILL MONTH"], parse_month)
    out["reading_date"] = doc.value(["READING DATE"], parse_date)
    out["issue_date"] = doc.value(["ISSUE DATE"], parse_date)
    for label in doc.find("DUE DATE", region=(W * 0.5, 0, W, H * 0.4), exact=True, cutoff=0.8):
        value, _ = doc.below(label, parse_date, max_rows=4)
        if value:
            out["due_date"] = value
            break
    out["sanctioned_load_kw"] = doc.value(["SAN LOAD", "SANC LOAD"], parse_number)
    for label in doc.find("TARIFF", exact=True, cutoff=0.8):
        if "categ" in label.norm:
            continue
        value, _ = doc.below(label, parse_tariff, max_rows=4, slack=80)
        if value:
            out["tariff"] = value
            break

    # ---- meter info
    prev_h = doc.find("PREVIOUS READING", region=(0, 0, W * 0.6, H * 0.5))
    pres_h = doc.find("PRESENT READING", region=(0, 0, W * 0.75, H * 0.5))
    if prev_h and pres_h:
        units_h = next(iter(doc.find("UNITS", region=(pres_h[0].x1, pres_h[0].y0 - 30, W * 0.75, pres_h[0].y1 + 30))), None)
        stop = doc.find("METER NO", "BILL CHARGES BREAKDOWN", region=(0, prev_h[0].y1, W * 0.75, H))
        y_end = min([t.y0 for t in stop if t.y0 > prev_h[0].y1] or [prev_h[0].y1 + H * 0.15])
        for row in doc.rows((0, prev_h[0].y1 + 2, (units_h.x1 + 150) if units_h else W * 0.7, y_end), tol=0.9):
            cell = lambda hdr: next((parse_amount(t.text) for t in row  # noqa: E731
                                     if hdr and t.x0 < hdr.x1 + 80 and t.x1 > hdr.x0 - 30
                                     and parse_number(t.text) is not None), None)
            prev, pres, units = cell(prev_h[0]), cell(pres_h[0]), cell(units_h)
            if prev is not None and pres is not None:
                ex["meter_rows"].append({"label": None, "previous": prev, "present": pres, "units": units})
    if ex["meter_rows"]:
        first = ex["meter_rows"][0]
        out["units_consumed"] = first["units"]
        if len(ex["meter_rows"]) == 1:
            out["previous_reading"], out["current_reading"] = first["previous"], first["present"]

    # ---- bill charges breakdown (label column + value column, matched by row)
    top = doc.find("BILL CHARGES BREAKDOWN")
    y0 = top[0].y1 if top else H * 0.3
    hist = doc.find("BILL HISTORY", region=(0, y0, W, H))
    y1 = hist[0].y0 if hist else y0 + H * 0.3
    arrears_lbl = next(iter(doc.find("Arrears", region=(0, y0, W, y1))), None)
    value_x1 = arrears_lbl.x0 + 10 if arrears_lbl else W * 0.45
    order = ["total", "subsidy", "net", "taxes", "current"]
    aliases = {
        "total": ["Total Electricity Charges", "TotalElectricity"], "subsidy": ["Subsidies"],
        "net": ["Net Electricity Charges", "NetElectricity"], "taxes": ["Taxes"], "current": ["Current Bill"],
    }
    region_left = (0, y0, value_x1 * 0.6, y1)
    grand = next(iter(doc.find("Grand Total", region=region_left, cutoff=0.7)), None)
    found = {k: next(iter(doc.find(*aliases[k], region=region_left, cutoff=0.62)), None) for k in order}
    y_stop = grand.y0 - 2 if grand else y1
    values = sorted((t for t in doc._in((value_x1 * 0.45, y0, value_x1, y_stop)) if parse_number(t.text) is not None),
                    key=lambda t: t.cy)
    grand_value = None
    if grand:  # photo offsets can push the Grand Total value above its label: claim it before aligning rows
        near = [t for t in values if abs(t.cy - grand.cy) <= grand.h * 1.6]
        if near:
            tok = min(near, key=lambda t: abs(t.cy - grand.cy))
            values.remove(tok)
            grand_value = parse_amount(tok.text)
        else:
            grand_value, _ = doc.right_of(grand, parse_number, dy=1.3)
    picked = align_rows(order, found, values)
    picked["grand"] = grand_value

    if picked.get("total"):
        out["charges"].append({"type": "energy", "amount": picked["total"]})
        ex["charge_lines"].append({"label": "Total Electricity Charges", "amount": picked["total"]})
    if picked.get("subsidy"):
        out["charges"].append({"type": "subsidy", "amount": -abs(picked["subsidy"])})
        ex["charge_lines"].append({"label": "Subsidies", "amount": -abs(picked["subsidy"])})
    fpa = doc.value(["Total FPA"], parse_number, where="right", region=(0, y0, W, y1))
    if fpa is None:
        for tok in doc.find("Total FPA", region=(0, y0, W, y1)):
            m = re.search(r"FPA\W*(-?\d[\d,.]*)", tok.text, re.I)
            if m:
                fpa = parse_amount(m.group(1))
                break
    if fpa:
        out["charges"].append({"type": "fpa", "amount": fpa})
        ex["charge_lines"].append({"label": "Total FPA", "amount": fpa})
    out["total_charges"] = picked.get("net")
    out["total_taxes"] = one_digit_repair(picked.get("taxes"), picked.get("current"), picked.get("net"))
    out["current_bill"] = picked.get("current")
    if arrears_lbl:
        row = [t for t in doc.tokens if t.x0 >= arrears_lbl.x1 - 5 and parse_number(t.text) is not None
               and abs(t.cy - arrears_lbl.cy) <= max(arrears_lbl.h, t.h) * 1.3]
        tok = min(row, key=lambda t: (abs(t.cy - arrears_lbl.cy), t.x0)) if row else None
        out["arrears"] = arrears_value(tok.text, picked.get("grand"), picked.get("current")) if tok else None

    # ---- right panel: payable within / after due date
    within = doc.value(["PAYABLE WITHIN"], parse_number, where="right", region=(W * 0.5, 0, W, H * 0.5), dy=1.4)
    out["payable_within_due_date"] = within if within is not None else picked.get("grand")
    after_lbl = next(iter(doc.find("AFTER DUE", region=(W * 0.5, 0, W, H * 0.5))), None)
    if after_lbl:
        zone = doc._in((after_lbl.x1, after_lbl.y0 - after_lbl.h * 2.2, W, after_lbl.y1 + after_lbl.h * 3))
        amounts = [parse_amount(t.text) for t in zone
                   if parse_number(t.text) is not None and "/" not in t.text and parse_date(t.text) is None]
        amounts = [a for a in amounts if a is not None and a != 0]
        if amounts:
            out["payable_after_due_date"] = max(amounts)
            ex["late_payment_amounts"] = [{"label": "payable after due date", "amount": a} for a in amounts]
    return out, ex


def align_rows(order: list[str], found: dict, values: list[Token]) -> dict:
    """Assign value tokens to a fixed sequence of table rows.

    Row positions come from the labels OCR did find (linear fit, so a garbled or missing label still gets
    a position); a global vertical shift is searched because photo bills print values ~half a row off.
    """
    known = [(order.index(k), t.cy) for k, t in found.items() if t is not None]
    if not values or not known:
        return {}
    if len(known) >= 2:
        n = len(known)
        mx, my = sum(i for i, _ in known) / n, sum(y for _, y in known) / n
        var = sum((i - mx) ** 2 for i, _ in known) or 1
        pitch = sum((i - mx) * (y - my) for i, y in known) / var
        base = my - pitch * mx
    else:
        pitch = values[0].h * 1.6
        base = known[0][1] - pitch * known[0][0]
    rows = [base + pitch * i for i in range(len(order))]
    best: tuple[float, dict] = (float("inf"), {})
    for step in range(-12, 13):
        shift = pitch * step / 20
        assigned, cost, taken = {}, 0.0, set()
        for v in values:
            dists = sorted((abs(v.cy - (r + shift)), i) for i, r in enumerate(rows) if i not in taken)
            if not dists or dists[0][0] > pitch * 0.5:
                cost += pitch  # a value that fits no row
                continue
            d, i = dists[0]
            taken.add(i)
            assigned[order[i]] = parse_amount(v.text)
            cost += d
        if cost < best[0]:
            best = (cost, assigned)
    return best[1]


def one_digit_repair(value, current, net):
    """OCR misread one digit of the printed tax figure (e.g. 8516 -> 3516): the printed current bill minus
    net charges gives the printed figure back. Only applied when exactly one digit differs."""
    if value is None or current is None or net is None:
        return value
    expected = current - net
    a, b = str(round(abs(value), 2)), str(round(abs(expected), 2))
    if abs(expected - value) > 1 and len(a) == len(b) and sum(x != y for x, y in zip(a, b)) == 1:
        return int(expected) if float(expected).is_integer() else round(expected, 2)
    return value


def arrears_value(text: str, grand: float | None, current: float | None):
    """'3941172' (OCR merged '39411/2') -> 39411 when the bill's own arithmetic confirms that prefix."""
    value = parse_number(text)
    if value is None or grand is None or current is None:
        return value
    expected = round(grand - current)
    digits = re.sub(r"\D", "", text)
    if abs(value - expected) > 1 and expected and digits.startswith(str(abs(expected))):
        return -abs(expected) if value < 0 else abs(expected)
    return value


# ============================================================== LESCO itemized layout
def is_itemized(doc: OcrDocument) -> bool:
    """Newer LESCO bills print three charge columns: LESCO CHARGES | GOVT. CHARGES | TOTAL CHARGES."""
    return bool(doc.find("LESCO CHARGES", cutoff=0.85)) and bool(doc.find("GOVT CHARGES", cutoff=0.85))


def _column_rows(doc: OcrDocument, x0: float, x1: float, y0: float, y1: float) -> list[tuple[Token, float]]:
    """(label token, value) pairs inside one column; each value is the closest number on the label's row."""
    tokens = doc._in((x0, y0, x1, y1))
    labels = [t for t in tokens if parse_number(t.text) is None and re.search(r"[A-Za-z]{2,}", t.text)]
    numbers = [t for t in tokens if parse_number(t.text) is not None]
    used: set[int] = set()
    pairs = []
    for lbl in sorted(labels, key=lambda t: t.cy):
        cands = [n for n in numbers if id(n) not in used and n.x0 > lbl.x0
                 and abs(n.cy - lbl.cy) <= max(lbl.h, n.h) * 0.9]
        if cands:
            best = min(cands, key=lambda n: abs(n.cy - lbl.cy))
            used.add(id(best))
            pairs.append((lbl, parse_amount(best.text)))
        else:
            pairs.append((lbl, None))
    return pairs


def parse_lesco_itemized(doc: OcrDocument) -> tuple[dict, dict]:
    W, H = doc.width, doc.height
    out: dict = {"charges": [], "taxes": []}
    ex: dict = {"charge_lines": [], "tax_lines": [], "late_payment_amounts": [], "meter_rows": [],
                "balance_lines": []}
    top = (0, 0, W, H * 0.2)
    out["tariff"] = doc.value(["TARIFF"], parse_tariff, region=top)
    out["sanctioned_load_kw"] = doc.value(["S.LOAD", "S LOAD"], parse_number, region=top)
    out["bill_month"] = doc.value(["BILL MONTH"], parse_month, region=top)
    out["reading_date"] = doc.value(["Reading Date"], parse_date, region=top)
    out["issue_date"] = doc.value(["ISSUE DATE"], parse_date, region=top)
    out["due_date"] = doc.value(["DUE DATE"], parse_date, region=top)
    out["previous_reading"] = doc.value(["PREVIOUS"], parse_number, region=(0, 0, W * 0.5, H * 0.45))
    out["current_reading"] = doc.value(["PRESENT"], parse_number, region=(0, 0, W * 0.5, H * 0.45))

    lesco = doc.find("LESCO CHARGES", cutoff=0.85)[0]
    govt = doc.find("GOVT CHARGES", cutoff=0.85)[0]
    total_hdr = next(iter(doc.find("TOTAL CHARGES", region=(govt.x1, govt.y0 - 40, W, govt.y1 + 40))), None)
    y0, y1 = lesco.y1, H * 0.85
    arrear = next(iter(doc.find("ARREAR", region=(govt.x1, y0, W, y0 + H * 0.1))), None)
    col_total = arrear.x0 - 10 if arrear else (total_hdr.x0 - 120 if total_hdr else W * 0.6)

    for lbl, value in _column_rows(doc, 0, govt.x0 - 10, y0, y1):          # LESCO CHARGES
        n = lbl.norm
        if "unitsconsumed" in n:
            out["units_consumed"] = value
        elif n.startswith("total"):
            out["total_charges"] = value
            break  # rows below TOTAL belong to the bill-calculation box, not the charges
        elif value:
            out["charges"].append({"type": lbl.text, "amount": value})
            ex["charge_lines"].append({"label": lbl.text, "amount": value})
    for lbl, value in _column_rows(doc, govt.x0 - 10, col_total, y0, y1):    # GOVT. CHARGES
        if lbl.norm.startswith("total"):
            out["total_taxes"] = value
            break
        elif value:
            out["taxes"].append({"type": lbl.text, "amount": value})
            ex["tax_lines"].append({"label": lbl.text, "amount": value})
    for lbl, value in _column_rows(doc, col_total, W, y0, y1):               # TOTAL CHARGES
        n = lbl.norm
        if "currentbill" in n:
            out["current_bill"] = value
        elif "arrear" in n:
            out["arrears"] = value
        elif "payablewithin" in n:
            out["payable_within_due_date"] = value
        elif value is not None:
            ex["balance_lines"].append({"label": lbl.text, "amount": value})
    for lbl in doc.find("PAYABLE AFTER DUE DATE", region=(col_total, y0, W, y1)):
        zone = doc._in((lbl.x1, lbl.y0 - lbl.h, W, lbl.y1 + lbl.h * 2.5))
        vals = [parse_number(t.text) for t in zone if "/" not in t.text and parse_date(t.text) is None]
        vals = [v for v in vals if v]
        if vals:
            out["payable_after_due_date"] = max(vals)
            ex["late_payment_amounts"] = [{"label": "payable after due date", "amount": v} for v in vals]
            break
    if out.get("previous_reading") is not None and out.get("current_reading") is not None:
        ex["meter_rows"].append({"label": None, "previous": out["previous_reading"],
                                 "present": out["current_reading"], "units": out.get("units_consumed")})
    return out, ex


# ============================================================== public API
class OcrExtractor:
    def __init__(self, cache_dir: Path):
        self.engine = OcrEngine(cache_dir)

    def history_crop(self, data: bytes) -> bytes | None:
        """PNG of just the usage/billing-history region (used to re-read history more reliably), or None."""
        doc = self.engine.read(data)[0]
        labels = doc.find("Usage History", "BILL HISTORY", "Payment History", cutoff=0.8)
        if labels:
            top = min(t.y0 for t in labels) - 10
            bottom = min(doc.height, max(t.y1 for t in labels) + doc.height * 0.30)
            box = (0, max(0, top), doc.width, bottom)
        else:  # history table identified by its column headers (e.g. MONTH ... PAYMENT)
            month = next(iter(doc.find("MONTH", exact=True, cutoff=0.9)), None)
            pay = next(iter(doc.find("PAYMENT", exact=True, cutoff=0.9)), None)
            if not (month and pay and abs(month.cy - pay.cy) < month.h * 2):
                return None
            box = (max(0, month.x0 - 20), max(0, month.y0 - 10), min(doc.width, pay.x1 + 40),
                   min(doc.height, month.y1 + doc.height * 0.2))
        img = self.engine.prepare(data).crop(tuple(int(v) for v in box))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def extract(self, bill_id: str, data: bytes, provider: str | None) -> dict:
        """Returns {"level1": raw dict, "extras": dict} in the shape normalize.py expects."""
        docs = self.engine.read(data)
        if provider is None:
            joined = " ".join(t.text for t in docs[0].tokens[:40]).upper()
            provider = "KE" if "KE " in joined or "K-ELECTRIC" in joined else (
                "IESCO" if "ISLAMABAD" in joined else "LESCO" if "LAHORE" in joined else None)
        parse = parse_ke if provider == "KE" else (parse_lesco_itemized if is_itemized(docs[0]) else parse_disco)
        level1, extras = parse(docs[0])
        for doc in docs[1:]:  # the second OCR view only fills what the first one missed
            alt, alt_extras = parse(doc)
            for key, value in alt.items():
                if level1.get(key) in (None, []) and value not in (None, []):
                    level1[key] = value
            for key, value in alt_extras.items():
                if not extras.get(key) and value:
                    extras[key] = value
        level1["provider"] = provider
        extras["ocr_text"] = [t.text for t in docs[0].tokens if t.conf >= 0.6]
        log.info("%s: OCR read %d text boxes (%d view(s))", bill_id, len(docs[0].tokens), len(docs))
        return {"level1": level1, "extras": extras}
