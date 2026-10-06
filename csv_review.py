#!/usr/bin/env python3
"""Local, read-only CSV validation. Python 3.10+, standard library only."""
import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import tempfile
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

VERSION = "1.0.0"
MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 100_000


def schema_from(path):
    raw = path.read_bytes()
    if len(raw) > 100_000:
        raise ValueError("Schema exceeds 100 KB")
    schema = json.loads(raw)
    if not isinstance(schema, dict) or set(schema) - {"columns", "unique_key"}:
        raise ValueError("Schema must contain only columns and unique_key")
    columns = schema.get("columns")
    if not isinstance(columns, dict) or not columns:
        raise ValueError("columns must be a nonempty object")
    allowed = {"type", "required", "minimum", "maximum", "values"}
    for name, rule in columns.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Every column needs a nonempty name")
        if name in {"_record", "_issues"}:
            raise ValueError("Column name is reserved: " + name)
        if not isinstance(rule, dict) or set(rule) - allowed:
            raise ValueError("Invalid rules for column: " + name)
        kind = rule.get("type", "text")
        if kind not in {"text", "decimal", "date", "enum"}:
            raise ValueError("Unsupported type for column: " + name)
        if "required" in rule and type(rule["required"]) is not bool:
            raise ValueError("required must be true or false")
        if kind == "enum":
            if not isinstance(rule.get("values"), list) or not rule["values"]:
                raise ValueError("enum needs a nonempty values list")
            if not all(isinstance(v, str) for v in rule["values"]):
                raise ValueError("enum values must be strings")
        elif "values" in rule:
            raise ValueError("values is only supported for enum")
        for bound in ("minimum", "maximum"):
            if bound in rule:
                if kind != "decimal" or isinstance(rule[bound], bool):
                    raise ValueError("Bounds are only supported for decimals")
                value = Decimal(str(rule[bound]))
                if not value.is_finite():
                    raise ValueError("Bounds must be finite")
        if "minimum" in rule and "maximum" in rule:
            if Decimal(str(rule["minimum"])) > Decimal(str(rule["maximum"])):
                raise ValueError("minimum must not exceed maximum")
    keys = schema.get("unique_key", [])
    if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
        raise ValueError("unique_key must be a list of column names")
    if len(keys) != len(set(keys)) or any(k not in columns for k in keys):
        raise ValueError("unique_key contains duplicate or unknown columns")
    return schema, raw


def read_csv(path, encoding="utf-8-sig", delimiter=","):
    with path.open("rb") as source:
        raw = source.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Input exceeds 10 MiB")
    text = raw.decode(encoding, errors="strict")
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
    headers = next(reader, None)
    if not headers or any(not h.strip() for h in headers):
        raise ValueError("CSV must have nonempty headers")
    if len(set(headers)) != len(headers):
        raise ValueError("Duplicate CSV headers")
    records = []
    for number, values in enumerate(reader, 1):
        if number > MAX_ROWS:
            raise ValueError("Input exceeds 100,000 records")
        if len(values) != len(headers):
            raise ValueError(f"Record {number}: {len(values)} fields, expected {len(headers)}")
        records.append(dict(zip(headers, values)))
    return headers, records, raw


def validate(headers, rows, schema):
    columns = schema["columns"]
    if set(headers) != set(columns):
        missing = sorted(set(columns) - set(headers))
        extra = sorted(set(headers) - set(columns))
        raise ValueError(f"Header mismatch: missing={missing}, extra={extra}")
    keys = schema.get("unique_key", [])
    counts = Counter(tuple(row[k] for k in keys) for row in rows) if keys else {}
    reviewed = []
    for number, row in enumerate(rows, 1):
        issues = []
        if keys:
            if any(not row[k].strip() for k in keys):
                issues.append("missing_unique_key")
            elif counts[tuple(row[k] for k in keys)] > 1:
                issues.append("duplicate_unique_key")
        for name, rule in columns.items():
            value = row[name]
            if value != value.strip():
                issues.append(name + ":surrounding_whitespace")
            if not value.strip():
                if rule.get("required", False):
                    issues.append(name + ":required")
                continue
            if value.lstrip().startswith(("=", "+", "@")):
                issues.append(name + ":spreadsheet_formula")
            kind = rule.get("type", "text")
            if kind == "decimal":
                if not re.fullmatch(r"-?[0-9]+(?:\.[0-9]+)?", value):
                    issues.append(name + ":invalid_decimal")
                else:
                    num = Decimal(value)
                    if "minimum" in rule and num < Decimal(str(rule["minimum"])):
                        issues.append(name + ":below_minimum")
                    if "maximum" in rule and num > Decimal(str(rule["maximum"])):
                        issues.append(name + ":above_maximum")
            elif kind == "date":
                try:
                    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
                        raise ValueError()
                    date.fromisoformat(value)
                except ValueError:
                    issues.append(name + ":invalid_iso_date")
            elif kind == "enum" and value not in rule["values"]:
                issues.append(name + ":unexpected_value")
        reviewed.append({"record": number, "values": row, "issues": issues})
    return reviewed


def sheet_safe(value):
    """CSV exports are viewing copies; raw JSON retains exact source strings."""
    if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
        return "'" + value
    return value


def write_bundle(output, headers, reviewed, summary):
    if output.exists():
        raise ValueError("Output already exists; choose a new directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Only this process's temporary directory is cleaned on failure.
    with tempfile.TemporaryDirectory(prefix=".csv-review-", dir=output.parent) as name:
        stage = Path(name)
        for filename, select in (("accepted.csv", False), ("review.csv", True)):
            with (stage / filename).open("w", encoding="utf-8-sig", newline="") as target:
                writer = csv.writer(target)
                writer.writerow(["_record", "_issues"] + [sheet_safe(h) for h in headers])
                for item in reviewed:
                    if bool(item["issues"]) == select:
                        writer.writerow([item["record"], "; ".join(item["issues"])] +
                                        [sheet_safe(item["values"][h]) for h in headers])
        bundle = {"summary": summary, "records": reviewed}
        (stage / "audit.json").write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        lines = ["# CSV Review", "", "Checks use supplied rules. No values were inferred or corrected.", "",
                 f"Records: {summary['records']}", f"Accepted: {summary['accepted']}",
                 f"Need review: {summary['needs_review']}", "",
                 "CSV exports include source record numbers and protect formula-like cells with an apostrophe.",
                 "audit.json preserves original strings. Keep the audit private when processing client data.", "",
                 "Record numbers count logical CSV records, excluding the header; multiline fields may span lines."]
        (stage / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        # Atomic visibility of the complete bundle. Never merge with existing reports.
        if output.exists():
            raise ValueError("Output appeared during processing; choose a new directory")
        os.rename(stage, output)


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--encoding", default="utf-8-sig")
    parser.add_argument("--delimiter", default=",")
    args = parser.parse_args(argv)
    if not args.dry_run and args.output is None:
        parser.error("--output is required unless --dry-run is set")
    try:
        if len(args.delimiter) != 1 or args.delimiter in "\r\n\0":
            raise ValueError("Delimiter must be a single non-newline character")
        schema, schema_raw = schema_from(args.schema)
        headers, rows, raw = read_csv(args.input, args.encoding, args.delimiter)
        reviewed = validate(headers, rows, schema)
        flagged = sum(bool(item["issues"]) for item in reviewed)
        summary = {"tool_version": VERSION, "records": len(rows),
                   "accepted": len(rows) - flagged, "needs_review": flagged,
                   "input_sha256": hashlib.sha256(raw).hexdigest(),
                   "schema_sha256": hashlib.sha256(schema_raw).hexdigest(),
                   "csv_formula_protection": True, "dry_run": args.dry_run}
        if not args.dry_run:
            write_bundle(args.output, headers, reviewed, summary)
        print(json.dumps(summary, ensure_ascii=False))
        return 1 if flagged else 0
    except (ValueError, OSError, UnicodeError, LookupError, csv.Error, ArithmeticError) as error:
        print("CSV review failed: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(run())
