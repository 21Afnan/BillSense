"""End-to-end bill processing: image -> Level 1 JSON via OCR (+extras) -> Level 2 answers via Gemini."""
from __future__ import annotations

import io
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import Settings
from app.core.llm import LLMClient, LLMError
from app.services import prompts
from app.services.answers import answer_problems, deterministic_answers
from app.services.history import is_severe, validate_history
from app.services.metrics import compute_metrics
from app.services.ocr_extractor import OcrExtractor
from app.services.normalize import (consistency_issues, empty_level1, normalize_extras, normalize_level1,
                                    provider_from_bill_id, repair_credit_signs, resolve_meter_readings)

log = logging.getLogger(__name__)

MAX_SIDE = 3072
ANSWER_UNAVAILABLE = ("An answer could not be generated for this question because the language model "
                      "was unavailable; the extracted bill details are in level1.csv.")
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}


@dataclass
class BillResult:
    bill_id: str
    level1: dict
    extras: dict = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    reviewed: bool = False
    error: str | None = None

    def to_dict(self) -> dict:
        return {"bill_id": self.bill_id, "level1": self.level1, "extras": self.extras,
                "issues": self.issues, "reviewed": self.reviewed, "error": self.error}


def load_image(data: bytes) -> tuple[bytes, str]:
    """Fix EXIF rotation, drop alpha, cap size. Returns (bytes, mime)."""
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    if max(img.size) > MAX_SIDE:
        img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), "image/png"


def find_bill_image(bills_dir: Path, bill_id: str) -> Path | None:
    for ext in IMAGE_EXTS:
        for cand in (bills_dir / f"{bill_id}{ext}", bills_dir / f"{bill_id}{ext.upper()}"):
            if cand.exists():
                return cand
    return None


class BillPipeline:
    """Level 1 = local OCR + layout rules (no LLM). Level 2 = Gemini, created only when first needed."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.ocr = OcrExtractor(settings.cache_dir)
        self._llm: LLMClient | None = None

    @property
    def llm(self) -> LLMClient:
        if self._llm is None:
            try:
                self._llm = LLMClient(self.settings)
            except RuntimeError as err:  # no key / disallowed model: Level 2 degrades, the batch keeps going
                raise LLMError(str(err)) from err
        return self._llm

    @property
    def llm_stats(self) -> dict | None:
        return self._llm.stats if self._llm else None

    # ------------------------------------------------------------ Level 1
    def extract(self, bill_id: str, image_bytes: bytes) -> BillResult:
        try:
            raw = self.ocr.extract(bill_id, image_bytes, provider_from_bill_id(bill_id))
        except UnidentifiedImageError:
            log.error("%s: not a valid image file", bill_id)
            return BillResult(bill_id, empty_level1(bill_id), error="not a valid image file")
        except Exception as err:  # OCR failure: keep the batch going
            log.error("%s: extraction failed: %s", bill_id, err)
            return BillResult(bill_id, empty_level1(bill_id), error=str(err))
        level1, extras, issues = self._parse(raw, bill_id)
        return BillResult(bill_id, level1, extras, issues)

    def _read_history(self, result: BillResult, image: tuple[bytes, str], image_bytes: bytes) -> list[dict]:
        """LLM reads the history chart/table (vision); it is validated, re-read from a crop if needed,
        and only then used. Python does every count and average."""
        bill_id = result.bill_id

        def attempt(img: tuple[bytes, str], note: str = "") -> tuple[list[dict], list[str]] | None:
            try:
                raw = self.llm.generate_json(prompts.HISTORY_SYSTEM, prompts.HISTORY_PROMPT + note, img)
            except LLMError as err:
                log.warning("%s: could not read usage history: %s", bill_id, err)
                return None
            return validate_history(normalize_extras(raw).get("usage_history", []), result.level1)

        first = attempt(image)
        if first is None:
            return []
        rows, problems = first
        source, retried = "full image", False
        if is_severe(problems):
            crop = None
            try:
                crop = self.ocr.history_crop(image_bytes)
            except Exception as err:  # cropping is best-effort
                log.warning("%s: history crop failed: %s", bill_id, err)
            if crop:
                retried = True
                second = attempt((crop, "image/png"), "\n\nThis image is a crop of the history section only. "
                                 "A previous reading had these problems: " + "; ".join(problems))
                if second and len(second[1]) < len(problems):
                    rows, problems, source = second[0], second[1], "history crop"
        bm, units = result.level1.get("bill_month"), result.level1.get("units_consumed")
        if any("but the bill says" in p for p in problems):  # trust the printed current-month units
            rows = [r for r in rows if r["month"] != bm]
        result.extras["history_check"] = {"months": len(rows), "problems": problems, "source": source,
                                          "retried": retried, "current_month_units": units}
        log.info("%s: usage history %d months (%s)%s", bill_id, len(rows), source,
                 f"; issues: {'; '.join(problems)}" if problems else "")
        return rows

    @staticmethod
    def _parse(raw: dict, bill_id: str) -> tuple[dict, dict, list[str]]:
        """Raw extraction -> validated Level 1 dict, extras, and consistency issues."""
        level1 = normalize_level1(raw.get("level1", raw), bill_id)
        extras = normalize_extras(raw.get("extras"))
        level1 = repair_credit_signs(resolve_meter_readings(level1, extras))
        return level1, extras, consistency_issues(level1)

    # ------------------------------------------------------------ Level 2
    def answer(self, result: BillResult, questions: list[tuple[str, str]],
               image_bytes: bytes | None = None, deterministic: bool = True) -> dict[str, str]:
        """questions: [(question_id, question)] -> {question_id: answer}. Never raises; never returns blanks.

        deterministic=True answers the fixed competition questions from Python metrics where the bill data
        allows; only the rest (or every free-form question when False) goes to the LLM.
        """
        if not questions:
            return {}
        if result.error and not image_bytes:
            msg = ("The bill image could not be read, so this question cannot be answered from the bill.")
            return {qid: msg for qid, _ in questions}
        image = None
        if image_bytes:
            try:
                image = load_image(image_bytes)
            except Exception:
                image = None
        if image and not result.extras.get("usage_history"):
            result.extras["usage_history"] = self._read_history(result, image, image_bytes)

        sources: dict[str, str] = {}
        answers: dict[str, str] = {}
        if deterministic:
            for qid, text in deterministic_answers(result.level1, result.extras, questions).items():
                if not answer_problems(text):
                    answers[qid], sources[qid] = text, "python"
        pending = [(qid, q) for qid, q in questions if qid not in answers]
        if pending:
            for qid, text in self._llm_answers(result, pending, image).items():
                answers[qid], sources[qid] = text, "llm"
        for qid, _ in questions:
            if not answers.get(qid):
                answers[qid], sources[qid] = ANSWER_UNAVAILABLE, "unavailable"
        result.extras["answer_sources"] = sources
        return answers

    def _llm_answers(self, result: BillResult, questions: list[tuple[str, str]], image) -> dict[str, str]:
        """One LLM call for all pending questions; skipped ones are retried singly; answers are validated."""
        try:
            metrics = compute_metrics(result.level1, result.extras)
        except Exception as err:  # a metrics bug must not cost the whole bill its answers
            log.warning("%s: metrics failed (%s); answering from bill data only", result.bill_id, err)
            metrics = {}
        bill_data = json.dumps({"level1": result.level1,
                                "extras": {k: v for k, v in result.extras.items() if k != "ocr_text"}},
                               ensure_ascii=False)
        metrics_txt = json.dumps(metrics, ensure_ascii=False, indent=1)

        def ask(batch: list[tuple[str, str]]) -> dict[str, str]:
            prompt = prompts.ANSWER_PROMPT.format(bill_id=result.bill_id, bill_data=bill_data, metrics=metrics_txt,
                                                  questions="\n".join(f"{qid}: {q}" for qid, q in batch))
            raw = self.llm.generate_json(prompts.ANSWER_SYSTEM, prompt, image)
            wanted = {qid.upper(): qid for qid, _ in batch}
            got = {}
            for item in raw.get("answers", []) if isinstance(raw.get("answers"), list) else []:
                if isinstance(item, dict) and item.get("question_id") and item.get("answer"):
                    qid = wanted.get(str(item["question_id"]).strip().upper()) or (
                        batch[0][0] if len(batch) == 1 else None)
                    text = str(item["answer"]).strip()
                    if qid and not answer_problems(text):
                        got[qid] = text
            return got

        answers: dict[str, str] = {}
        try:
            answers.update(ask(questions))
        except LLMError as err:
            log.error("%s: answering failed: %s", result.bill_id, err)
            return answers
        for qid, q in [(qid, q) for qid, q in questions if qid not in answers]:
            try:
                answers.update(ask([(qid, q)]))
            except LLMError as err:
                log.error("%s %s: single answer failed: %s", result.bill_id, qid, err)
        return answers
