"""CSV helpers shared by the CLI and the API, following guide section 7.2."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path


class TemplateError(ValueError):
    pass


def read_csv(path: Path, required: tuple[str, ...] = ()) -> tuple[list[str], list[dict]]:
    """Read a CSV template; raise TemplateError with a clear message if it is missing or malformed."""
    if not path.is_file():
        raise TemplateError(f"template not found: {path}")
    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fields, rows = list(reader.fieldnames or []), list(reader)
    except (OSError, UnicodeDecodeError, csv.Error) as err:
        raise TemplateError(f"cannot read {path}: {err}") from err
    missing = [c for c in required if c not in fields]
    if missing:
        raise TemplateError(f"{path.name} is missing column(s) {missing}; found {fields}")
    if required and not rows:
        raise TemplateError(f"{path.name} has no rows")
    return fields, rows


def to_csv_text(fieldnames: list[str], rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


class OutputLockedError(RuntimeError):
    pass


def write_text_file(path: Path, text: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(text, encoding=encoding, newline="")
    except PermissionError as err:
        raise OutputLockedError(f"Cannot write {path}: it is open in another program (Excel?). "
                                "Close it and run again - results are cached, so no extra API calls.") from err


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    write_text_file(path, to_csv_text(fieldnames, rows))


def level1_cell(level1: dict) -> str:
    """One-line JSON for the level1.csv `json` column (the csv module doubles the quotes)."""
    return json.dumps(level1, ensure_ascii=False, separators=(",", ":"))
