# CSV Review, v1.0.0

[![Checks](https://github.com/Dun4ev/csv-review/actions/workflows/check.yml/badge.svg)](https://github.com/Dun4ev/csv-review/actions/workflows/check.yml)

A real local CSV validation tool for recurring operational exports. The included data is synthetic. This tool is a separate delivery asset; it does not enable OCR or AI in the existing Document Intake demo.

Python 3.10+; no dependencies, account, network requests or API keys. Source files are read-only. Input limit: 10 MiB and 100,000 logical records. The service offer has a smaller agreed scope.

## Need this adapted to your export?

[Message Andrey on Freelancer](https://www.freelancer.com/u/dun4ev) with an anonymized CSV, the desired output and the rules you currently check by hand. Choose a setup pilot or recurring review. Scope, delivery dates and price are confirmed before project acceptance.

| Service | Price (USD) | Included scope | What you receive |
| --- | ---: | --- | --- |
| Setup pilot | $100 once | One CSV, up to 10,000 rows and 10 MiB; one agreed schema and validation rule set | Configuration, repeatable local command, initial review report and instructions |
| Recurring review | $150 per month | Four CSV batches, each up to 10,000 rows and 10 MiB, using the same unchanged schema and rules | Four report bundles with passed rows, exceptions and exact source values; a short monthly issue summary |

The recurring service follows an agreed setup. Each report flags issues for your review; corrections, deduplication or enrichment require separately approved rules. New schemas, XLSX handling, OCR, master-list corrections and backend/API integration are separately scoped after sample review. Files are supplied and reports delivered through the agreed private project channel. Monthly work is agreed one month at a time through Freelancer.

Prices cover the service scope above. This repository and its MIT-licensed code remain free to use independently. The synthetic example demonstrates how the checks work; no client results or revenue are claimed. Please send an anonymized sample first, and keep confidential production data out of public repository issues.

## Run

From this folder, preview the checks:

```sh
python3 csv_review.py sample.csv --schema schema.json --dry-run
```

Write a new report directory:

```sh
python3 csv_review.py sample.csv --schema schema.json --output my-report
```

The synthetic sample has 12 records: 3 accepted and 9 requiring review. Exit status **1** means reports were produced but rows need review; **0** means every record passed; **2** means a fatal file/schema error and no new report bundle. Shell runners must not confuse a review outcome with a broken program.

Outputs: `accepted.csv`, `review.csv`, `audit.json`, `report.md`. Each CSV has `_record` and `_issues` before the source columns. Headers must match the schema exactly; their input order is preserved. Existing output directories are refused. A second run needs a new directory, which prevents accidental overwrite. Runs with the same input/schema yield the same content apart from the dry-run flag.

Checks: required values, duplicate composite keys (all colliding rows are held), surrounding whitespace, exact decimal syntax with optional bounds, strict ISO dates, allowed enum values and formula-like strings. Identifiers, revisions, decimal strings, unknown values and original record order are preserved. No guessing, locale conversion, correction or duplicate removal occurs. Users agree transformation rules separately before cleaning.

Set `--delimiter ';'` or `--encoding cp1251` explicitly when needed. Default is comma-separated UTF-8 with optional BOM. Bad encoding, malformed rows, duplicate/blank headers, missing or extra columns are fatal errors. Blank physical lines follow Python CSV parsing; record numbers refer to logical records excluding the header.

CSV exports are viewing copies: potentially executable spreadsheet cells (including names of columns) get a leading apostrophe. This can alter imported strings; use `audit.json` for exact source values. Review does not assert accounting correctness, engineering approval or business truth. The rules only verify specified data conditions.

Do not publish audit bundles containing client data. Only authorized, necessary datasets should be processed, with agreed retention and delivery. The seven-test suite passed on Linux, macOS and Windows with Python 3.10 and 3.14. [Completed cross-platform run](https://github.com/Dun4ev/csv-review/actions/runs/37549070431). Confidentiality arrangements and the client's lawful authority over data must be settled before receiving client files.

## Verification

```sh
python3 -m unittest discover -s tests -v
```

Seven tests cover the synthetic example, original-file integrity, duplicate collisions, exact identifiers, dates/decimals, malformed files, encoding, multiline fields, size limits, rerun refusal and spreadsheet export protection. The Actions workflow runs this suite on Linux, macOS and Windows with Python 3.10 and 3.14. An actual completed workflow run is needed to confirm each platform; configuration alone is not proof.

License: [MIT](LICENSE). The paid service concerns agreed customization and operation, not exclusive rights to this freely available code.
