"""Batch runner: read bill images + CSV templates, write completed level1.csv and level2.csv.

Example (from source/backend):
    python run_batch.py --bills ../../test/bills --level1 ../../test/csv/level1.csv \
        --level2 ../../test/csv/level2.csv --out ../../output
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import OrderedDict
from pathlib import Path

from app.core.config import PROJECT_DIR, get_settings
from app.services.csv_io import OutputLockedError, TemplateError, level1_cell, read_csv, write_csv
from app.services.normalize import empty_level1
from app.services.pipeline import ANSWER_UNAVAILABLE, IMAGE_EXTS, BillPipeline, BillResult, find_bill_image
from app.services.readable import write_readable

log = logging.getLogger("run_batch")


def show_bill(bill_id: str, results: dict[str, BillResult], l2_rows: list[dict]) -> None:
    """Readable console summary of one bill (used for demos)."""
    res = results.get(bill_id)
    if res is None:
        log.error("--show: %s is not in this run", bill_id)
        return
    line = "=" * 78
    print(f"\n{line}\n {bill_id}  -  LEVEL 1: extracted fields\n{line}")
    for key, value in res.level1.items():
        if key in ("charges", "taxes"):
            value = ", ".join(f"{i['type']} {i['amount']:,}" for i in value) or "[]"
        print(f"  {key:<26} {value}")
    qa = [r for r in l2_rows if r["bill_id"].strip() == bill_id]
    if qa:
        print(f"\n{line}\n {bill_id}  -  LEVEL 2: answers\n{line}")
        for r in qa:
            print(f"\n  {r['question_id']}. {r['question']}\n     {r['answer']}")
    print(line)


def main() -> int:
    ap = argparse.ArgumentParser(description="Utility Bill Decoder - batch mode")
    ap.add_argument("--bills", required=True, type=Path, help="folder with bill images, or one bill image")
    ap.add_argument("--level1", type=Path, help="level1.csv template (bill_id,json)")
    ap.add_argument("--level2", type=Path, help="level2.csv template (bill_id,question_id,question,answer)")
    ap.add_argument("--out", type=Path, default=PROJECT_DIR / "output", help="output folder (default: <project>/output)")
    ap.add_argument("--show", metavar="BILL_ID", help="after the run, print this bill's Level 1 fields and Q&A")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    settings = get_settings()
    try:
        pipeline = BillPipeline(settings)
    except RuntimeError as err:
        log.error("%s", err)
        return 2
    log.info("Level 1: local OCR (no LLM). Level 2: %s, fallback %s", settings.model_name, settings.fallback_model)

    # ---- which bills, in which order
    if not args.bills.exists():
        log.error("not found: %s", args.bills)
        return 2
    single = args.bills.is_file()
    if single:  # one image: process just that bill
        if args.bills.suffix.lower() not in IMAGE_EXTS:
            log.error("not a bill image (.png/.jpg/.jpeg/.webp): %s", args.bills)
            return 2
        bill_ids = [args.bills.stem]
        args.bills = args.bills.parent
        l1_fields, l1_rows = ["bill_id", "json"], [{"bill_id": bill_ids[0], "json": ""}]
    elif args.level1:
        l1_fields, l1_rows = read_csv(args.level1, required=("bill_id", "json"))
        bill_ids = [r["bill_id"].strip() for r in l1_rows]
    else:
        l1_fields = ["bill_id", "json"]
        bill_ids = sorted(p.stem for p in args.bills.iterdir() if p.suffix.lower() in IMAGE_EXTS)
        l1_rows = [{"bill_id": b, "json": ""} for b in bill_ids]
    l2_fields, l2_rows = (read_csv(args.level2, required=("bill_id", "question_id", "question", "answer"))
                          if args.level2 else ([], []))
    if not bill_ids and not l2_rows:
        log.error("no bill images found in %s", args.bills)
        return 2
    for r in l2_rows:
        if r["bill_id"].strip() not in bill_ids:
            bill_ids.append(r["bill_id"].strip())

    # ---- Level 1
    results: dict[str, BillResult] = {}
    images: dict[str, bytes] = {}
    for n, bill_id in enumerate(OrderedDict.fromkeys(bill_ids), start=1):
        path = find_bill_image(args.bills, bill_id)
        if path is None:
            log.error("[%d/%d] %s: image not found in %s", n, len(bill_ids), bill_id, args.bills)
            results[bill_id] = BillResult(bill_id, empty_level1(bill_id), error="image not found")
            continue
        images[bill_id] = path.read_bytes()
        log.info("[%d/%d] extracting %s", n, len(bill_ids), bill_id)
        results[bill_id] = pipeline.extract(bill_id, images[bill_id])
        res = results[bill_id]
        if res.issues:
            log.info("   remaining checks for %s: %s", bill_id, "; ".join(res.issues))

    args.out.mkdir(parents=True, exist_ok=True)
    if single:  # never overwrite a full level1.csv with a one-bill run
        res = results[bill_ids[0]]
        out_file = args.out / f"{res.bill_id}.json"
        out_file.write_text(json.dumps(res.level1, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(res.level1, ensure_ascii=False, indent=2))
        log.info("wrote %s", out_file)
    else:
        for row in l1_rows:
            row["json"] = level1_cell(results[row["bill_id"].strip()].level1)
        write_csv(args.out / "level1.csv", l1_fields, l1_rows)
        log.info("wrote %s (%d rows) - official submission file", args.out / "level1.csv", len(l1_rows))
    for path in write_readable(args.out, list(results.values())):
        log.info("wrote %s - human-readable copy", path)

    # Debug artefact: full extraction incl. extras and check results.
    debug = {b: r.to_dict() for b, r in results.items()}
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    (settings.cache_dir / "last_run.json").write_text(
        json.dumps(debug, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- Level 2
    if l2_rows:
        by_bill: dict[str, list[tuple[str, str]]] = OrderedDict()
        for r in l2_rows:
            by_bill.setdefault(r["bill_id"].strip(), []).append((r["question_id"].strip(), r["question"]))
        all_answers: dict[tuple[str, str], str] = {}
        for n, (bill_id, qs) in enumerate(by_bill.items(), start=1):
            log.info("[%d/%d] answering %d questions for %s", n, len(by_bill), len(qs), bill_id)
            answers = pipeline.answer(results[bill_id], qs, images.get(bill_id))
            for qid, ans in answers.items():
                all_answers[(bill_id, qid)] = ans
        for r in l2_rows:
            r["answer"] = all_answers.get((r["bill_id"].strip(), r["question_id"].strip())) or \
                "The bill could not be processed, so this question cannot be answered."
        write_csv(args.out / "level2.csv", l2_fields, l2_rows)
        log.info("wrote %s (%d rows)", args.out / "level2.csv", len(l2_rows))

    if args.show:
        show_bill(args.show, results, l2_rows)

    failed = [b for b, r in results.items() if r.error]
    unanswered = sum(1 for r in l2_rows if r.get("answer") == ANSWER_UNAVAILABLE)
    if pipeline.llm_stats:
        log.info("model usage: %s", pipeline.llm_stats)
    if failed:
        log.warning("bills that could not be read (written with null fields): %s", ", ".join(failed))
    if unanswered:
        log.warning("%d of %d answers could not be generated (LLM unavailable) - fix the key/network and re-run",
                    unanswered, len(l2_rows))
    if failed or unanswered:
        return 1
    log.info("done: every bill extracted%s", " and every question answered" if l2_rows else "")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OutputLockedError, TemplateError) as err:
        log.error("%s", err)
        sys.exit(3)
    except KeyboardInterrupt:
        log.error("interrupted; finished results are cached, re-run to continue")
        sys.exit(130)
