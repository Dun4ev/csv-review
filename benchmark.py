#!/usr/bin/env python3
"""Reproducible synthetic end-to-end benchmark for the CSV review CLI."""
import argparse
import csv
import hashlib
import json
import platform
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path
from time import perf_counter

HERE = Path(__file__).resolve().parent
CLI = HERE / "csv_review.py"
SCHEMA = HERE / "schema.json"
HEADERS = ["document_id", "revision", "document_date", "amount", "currency", "status"]
EXPECTED_ISSUES = {
    1: ["duplicate_unique_key"],
    2: ["duplicate_unique_key"],
    3: ["document_date:invalid_iso_date"],
    4: ["amount:below_minimum"],
    5: ["document_id:spreadsheet_formula"],
}


def bounded(minimum, maximum):
    def parse(value):
        try:
            number = int(value)
        except ValueError as error:
            raise argparse.ArgumentTypeError("must be an integer") from error
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(f"must be between {minimum} and {maximum}")
        return number
    return parse


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def fixture_rows(count):
    rows = [
        dict(document_id="DOC-DUP", revision="00", document_date="2026-01-01",
             amount="10.00", currency="EUR", status="approved"),
        dict(document_id="DOC-DUP", revision="00", document_date="2026-01-02",
             amount="11.00", currency="EUR", status="approved"),
        dict(document_id="DOC-DATE", revision="00", document_date="2026-02-30",
             amount="12.00", currency="USD", status="review"),
        dict(document_id="DOC-AMOUNT", revision="00", document_date="2026-02-04",
             amount="-1.00", currency="RSD", status="draft"),
        dict(document_id="=1+1", revision="00", document_date="2026-02-05",
             amount="13.00", currency="USD", status="approved"),
    ]
    rows.extend(dict(document_id=f"DOC-{number:06d}", revision="00",
                     document_date="2026-03-01", amount="1.00",
                     currency="EUR", status="approved")
                for number in range(6, count + 1))
    return rows


def verify_bundle(output, cli_stdout, expected_rows, source_sha, schema_sha, expected_count):
    summary = json.loads(cli_stdout)
    expected = (expected_count, expected_count - 5, 5, source_sha, schema_sha, True, False)
    actual = (summary.get("records"), summary.get("accepted"), summary.get("needs_review"),
              summary.get("input_sha256"), summary.get("schema_sha256"),
              summary.get("csv_formula_protection"), summary.get("dry_run"))
    if actual != expected or not isinstance(summary.get("tool_version"), str):
        raise AssertionError("CLI summary did not match the expected counts and hashes")
    audit = json.loads((output / "audit.json").read_text(encoding="utf-8"))
    records = audit.get("records", [])
    if len(records) != expected_count or [item.get("values") for item in records] != expected_rows:
        raise AssertionError("audit.json did not preserve all raw input values")
    actual_issues = {item["record"]: item["issues"] for item in records if item["issues"]}
    if actual_issues != EXPECTED_ISSUES:
        raise AssertionError(f"unexpected record numbers or issue codes: {actual_issues!r}")
    if audit.get("summary") != summary:
        raise AssertionError("audit summary differs from CLI summary")
    with (output / "accepted.csv").open(encoding="utf-8-sig", newline="") as source:
        accepted = list(csv.DictReader(source))
    with (output / "review.csv").open(encoding="utf-8-sig", newline="") as source:
        review = list(csv.DictReader(source))
    if len(accepted) != expected_count - 5 or len(review) != 5:
        raise AssertionError("accepted.csv/review.csv record counts are wrong")
    if [int(row["_record"]) for row in review] != list(EXPECTED_ISSUES):
        raise AssertionError("review.csv record numbers are wrong")
    if [row["_issues"] for row in review] != ["; ".join(EXPECTED_ISSUES[n]) for n in EXPECTED_ISSUES]:
        raise AssertionError("review.csv issue codes are wrong")
    formula_row = next(row for row in review if int(row["_record"]) == 5)
    if formula_row["document_id"] != "'=1+1":
        raise AssertionError("formula-like viewing copy was not protected")


def run(rows, repeat):
    source_code_sha = sha256(CLI.read_bytes())
    schema_sha = sha256(SCHEMA.read_bytes())
    input_hash = None
    input_bytes = 0
    timings = []
    expected_rows = fixture_rows(rows)
    with tempfile.TemporaryDirectory(prefix="csv-service-benchmark-") as temporary:
        root = Path(temporary)
        source = root / "synthetic.csv"
        with source.open("w", encoding="utf-8", newline="") as target:
            writer = csv.writer(target, lineterminator="\n")
            writer.writerow(HEADERS)
            writer.writerows([row[name] for name in HEADERS] for row in expected_rows)
        original_source_sha = sha256(source.read_bytes())
        input_hash = original_source_sha
        input_bytes = source.stat().st_size
        for index in range(repeat):
            output = root / f"bundle-{index + 1}"
            command = [sys.executable, str(CLI), str(source), "--schema", str(SCHEMA),
                       "--output", str(output)]
            started = perf_counter()
            process = subprocess.run(command, capture_output=True, text=True, check=False)
            timings.append(perf_counter() - started)
            if process.returncode != 1:
                raise AssertionError(f"CLI exit was {process.returncode}: {process.stderr.strip()}")
            if sha256(source.read_bytes()) != original_source_sha:
                raise AssertionError("source CSV changed during CLI execution")
            verify_bundle(output, process.stdout, expected_rows, original_source_sha, schema_sha, rows)
    return {
        "synthetic": True,
        "rows": rows,
        "accepted": rows - 5,
        "needs_review": 5,
        "input_bytes": input_bytes,
        "input_sha256": input_hash,
        "source_code_sha256": source_code_sha,
        "schema_sha256": schema_sha,
        "elapsed_seconds": timings,
        "median_seconds": statistics.median(timings),
        "python_version": platform.python_version(),
        "os_architecture": f"{platform.system()} {platform.machine()}",
        "assertions": {
            "exit_code_1_each_run": True,
            "expected_counts_each_run": True,
            "record_numbers_and_issue_codes_each_run": True,
            "source_sha_unchanged_each_run": True,
            "audit_preserves_all_raw_values_each_run": True,
            "csv_record_counts_each_run": True,
            "formula_viewing_copy_protected_each_run": True,
        },
        "scope": "Synthetic workload only; no human-time or ROI comparison and no universal throughput guarantee.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=bounded(10, 100_000), default=10_000)
    parser.add_argument("--repeat", type=bounded(1, 5), default=3)
    args = parser.parse_args()
    print(json.dumps(run(args.rows, args.repeat), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
