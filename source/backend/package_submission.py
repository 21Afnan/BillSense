"""Build the competition ZIP exactly as guide section 7 asks, and run the section 7.8 checklist.

    submission/
    ├── output/  level1.csv, level2.csv
    ├── source/  everything in the project's source/ folder (README.md, .env.example, requirements.txt, backend/)
    └── demo/    demo.mp4 (optional - copied if present in the project root)

Usage:  python package_submission.py            (from source/backend)
"""
from __future__ import annotations

import csv
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]      # project folder: .env, output/, source/
SOURCE = ROOT / "source"
BUILD = ROOT / "submission"
ZIP_PATH = ROOT / "submission.zip"
MAX_ZIP_MB = 15
SKIP = {".env", ".env.local", "venv", ".venv", ".cache", "node_modules", ".next", "__pycache__", "output",
        "submission", "test", "bills", "tsconfig.tsbuildinfo"}
DEMO_NAMES = ["demo.mp4", "demo.mov", "demo.webm"]


def check_csv(path: Path, value_col: str, expected_rows: int | None) -> list[str]:
    if not path.exists():
        return [f"missing {path.relative_to(ROOT)}"]
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    problems = []
    if expected_rows and len(rows) != expected_rows:
        problems.append(f"{path.name}: {len(rows)} rows, expected {expected_rows}")
    empty = [r.get("bill_id", "?") for r in rows if not (r.get(value_col) or "").strip()]
    if empty:
        problems.append(f"{path.name}: empty '{value_col}' cells for {', '.join(empty[:5])}")
    return problems


def copy_tree(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, ignore=lambda _dir, names: [n for n in names if n in SKIP])


def main() -> int:
    problems = check_csv(ROOT / "output" / "level1.csv", "json", 10)
    problems += check_csv(ROOT / "output" / "level2.csv", "answer", 120)
    secrets = [p for p in (ROOT / ".env", SOURCE / ".env") if p.exists()]

    if BUILD.exists():
        shutil.rmtree(BUILD)
    (BUILD / "output").mkdir(parents=True)
    for name in ("level1.csv", "level2.csv"):
        if (ROOT / "output" / name).exists():
            shutil.copy2(ROOT / "output" / name, BUILD / "output" / name)
    copy_tree(SOURCE, BUILD / "source")  # the whole source/ folder, minus caches and secrets
    demo = next((ROOT / n for n in DEMO_NAMES if (ROOT / n).exists()), None)
    if demo:
        (BUILD / "demo").mkdir()
        shutil.copy2(demo, BUILD / "demo" / demo.name)

    leaked = [p for p in BUILD.rglob("*") if p.name in (".env", ".env.local")]
    if leaked:
        problems.append(f"secret files inside the package: {leaked}")
    secret_values = set()
    for env_file in secrets:  # every real value from local .env files (keys are never printed)
        for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            name, value = (part.strip().strip("'\"") for part in line.split("=", 1))
            if any(word in name.upper() for word in ("KEY", "TOKEN", "SECRET")) and len(value) >= 20:
                secret_values.add(value.encode())
    hits = [p.relative_to(BUILD) for p in BUILD.rglob("*") if p.is_file()
            and any(v in p.read_bytes() for v in secret_values)]
    if hits:
        problems.append(f"a real API key value appears inside: {hits}")

    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(BUILD.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(BUILD))
    size_mb = ZIP_PATH.stat().st_size / 1024 / 1024
    if size_mb > MAX_ZIP_MB:
        problems.append(f"ZIP is {size_mb:.1f} MB (limit {MAX_ZIP_MB} MB)")

    print(f"built {BUILD.relative_to(ROOT)}/ and {ZIP_PATH.name} ({size_mb:.2f} MB)")
    with zipfile.ZipFile(ZIP_PATH) as zf:
        tops = sorted({n.split("/")[0] + "/" + (n.split("/")[1] if "/" in n else "") for n in zf.namelist()})
    print("contents:", ", ".join(tops))
    print("demo video:", demo.name if demo else "none (optional, up to +5 points)")
    print(f"secret scan: checked {len(secret_values)} key value(s) from local .env, found in {len(hits)} file(s)")
    if secrets:
        print(f"note: {len(secrets)} local .env file(s) exist but were NOT packaged")
    if problems:
        print("\nNOT READY TO SUBMIT:")
        for p in problems:
            print("  -", p)
        return 1
    print("\nREADY: all checklist items passed. Open the ZIP once before uploading (you can submit only once).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
