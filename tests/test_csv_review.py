import contextlib
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

PRODUCT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("csv_review", PRODUCT / "csv_review.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


class ReviewTests(unittest.TestCase):
    def run_tool(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = tool.run(list(map(str, args)))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_example_integrity_outputs_and_collision(self):
        original = (PRODUCT / "sample.csv").read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "result"
            args = (PRODUCT / "sample.csv", "--schema", PRODUCT / "schema.json", "--output", output)
            code, stdout, stderr = self.run_tool(*args)
            summary = json.loads(stdout)
            self.assertEqual((code, summary["accepted"], summary["needs_review"]), (1, 3, 9))
            self.assertEqual(stderr, "")
            self.assertEqual(summary["input_sha256"], hashlib.sha256(original).hexdigest())
            self.assertEqual(original, (PRODUCT / "sample.csv").read_bytes())
            bundle = json.loads((output / "audit.json").read_text(encoding="utf-8"))
            self.assertEqual(bundle["records"][2]["issues"], ["duplicate_unique_key"])
            self.assertEqual(bundle["records"][3]["issues"], ["duplicate_unique_key"])
            self.assertEqual(bundle["records"][0]["values"]["revision"], "00")
            self.assertEqual(bundle["records"][-1]["values"]["document_id"], "=1+1")
            self.assertIn("'=1+1", (output / "review.csv").read_text(encoding="utf-8-sig"))
            before = {p.name: p.read_bytes() for p in output.iterdir()}
            self.assertEqual(self.run_tool(*args)[0], 2)
            self.assertEqual(before, {p.name: p.read_bytes() for p in output.iterdir()})
            second = Path(tmp) / "second"
            self.assertEqual(self.run_tool(*args[:-1], second)[0], 1)
            self.assertEqual(before, {p.name: p.read_bytes() for p in second.iterdir()})

    def test_dry_run_never_creates_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "unused"
            code, stdout, _ = self.run_tool(PRODUCT / "sample.csv", "--schema", PRODUCT / "schema.json", "--dry-run", "--output", output)
            self.assertEqual(code, 1)
            self.assertTrue(json.loads(stdout)["dry_run"])
            self.assertFalse(output.exists())

    def test_reject_invalid_csv_and_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            input_file, schema_file, output = tmp / "input.csv", tmp / "schema.json", tmp / "out"
            schema_file.write_text(json.dumps({"columns": {"id": {"required": True}}, "unique_key": ["id"]}))
            for text in ("id,id\na,b\n", "id\na,b\n", "wrong\na\n", 'id\n"unclosed'):
                input_file.write_text(text)
                self.assertEqual(self.run_tool(input_file, "--schema", schema_file, "--output", output)[0], 2)
                self.assertFalse(output.exists())
            input_file.write_bytes(b"id\n\xff\n")
            self.assertEqual(self.run_tool(input_file, "--schema", schema_file, "--output", output)[0], 2)
            input_file.write_text("id\n001\n")
            schema_file.write_text('{"columns":{"id":{"required":"yes"}}}')
            self.assertEqual(self.run_tool(input_file, "--schema", schema_file, "--output", output)[0], 2)

    def test_types_identifiers_and_empty_optionals(self):
        schema = {"columns": {"id": {}, "n": {"type": "decimal", "minimum": "0", "maximum": "10"}, "d": {"type": "date"}}, "unique_key": ["id"]}
        rows = [
            {"id": "001", "n": "1.00", "d": "2024-02-29"},
            {"id": "002", "n": "NaN", "d": "2023-02-29"},
            {"id": "003", "n": "Infinity", "d": "2026-1-01"},
            {"id": "004", "n": "10.01", "d": ""},
            {"id": "005", "n": "", "d": ""},
            {"id": " 006 ", "n": "1e2", "d": "2026-01-01"},
        ]
        result = tool.validate(list(schema["columns"]), rows, schema)
        self.assertEqual(result[0]["issues"], [])
        self.assertEqual(result[0]["values"]["id"], "001")
        self.assertIn("n:invalid_decimal", result[1]["issues"])
        self.assertIn("d:invalid_iso_date", result[1]["issues"])
        self.assertIn("n:invalid_decimal", result[2]["issues"])
        self.assertIn("n:above_maximum", result[3]["issues"])
        self.assertEqual(result[4]["issues"], [])
        self.assertIn("id:surrounding_whitespace", result[5]["issues"])

    def test_semicolon_encoding_multiline_and_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            input_file, schema_file = tmp / "input.csv", tmp / "schema.json"
            input_file.write_bytes('id;description\r\n001;"Текст\nвторая строка"\r\n'.encode("cp1251"))
            schema_file.write_text(json.dumps({"columns": {"id": {}, "description": {}}, "unique_key": ["id"]}))
            code, stdout, _ = self.run_tool(input_file, "--schema", schema_file, "--delimiter", ";", "--encoding", "cp1251", "--output", tmp / "out")
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout)["records"], 1)
            audit = json.loads((tmp / "out" / "audit.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["records"][0]["values"]["description"], "Текст\nвторая строка")

    def test_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_file = Path(tmp) / "large.csv"
            input_file.write_bytes(b"id\n" + b"x" * tool.MAX_BYTES)
            with self.assertRaisesRegex(ValueError, "10 MiB"):
                tool.read_csv(input_file)
            prior = tool.MAX_ROWS
            try:
                tool.MAX_ROWS = 1
                input_file.write_text("id\na\nb\n")
                with self.assertRaisesRegex(ValueError, "records"):
                    tool.read_csv(input_file)
            finally:
                tool.MAX_ROWS = prior

    def test_csv_protection(self):
        for value in ("=1+1", " +SUM(A1)", "-1", "@foo", "\tvalue", "\nvalue"):
            self.assertTrue(tool.sheet_safe(value).startswith("'"))
        self.assertEqual(tool.sheet_safe("001"), "001")


if __name__ == "__main__":
    unittest.main()
