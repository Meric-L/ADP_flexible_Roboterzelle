"""Tests fuer `tagloc.jsonio`: atomares Schreiben und Lesen mit Schema-Pruefung."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tagloc import jsonio


class WriteJsonTest(unittest.TestCase):
    def test_creates_missing_folders_and_ends_with_a_newline(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "a" / "b" / "datei.json"

            jsonio.write_json(path, {"schema": "x/1", "wert": [1, 2]})
            text = path.read_text(encoding="utf-8")

        self.assertEqual(text, json.dumps({"schema": "x/1", "wert": [1, 2]}, indent=2) + "\n")

    def test_leaves_no_temporary_file_behind(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "datei.json"

            jsonio.write_json(path, {"a": 1})
            jsonio.write_json(path, {"a": 2})
            names = sorted(item.name for item in Path(folder).iterdir())

        self.assertEqual(names, ["datei.json"])

    def test_keeps_the_old_file_when_writing_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "datei.json"
            jsonio.write_json(path, {"alt": True})

            with mock.patch.object(jsonio.os, "replace", side_effect=OSError("voll")):
                with self.assertRaises(OSError):
                    jsonio.write_json(path, {"neu": True})
            content = json.loads(path.read_text(encoding="utf-8"))
            names = sorted(item.name for item in Path(folder).iterdir())

        self.assertEqual(content, {"alt": True})
        self.assertEqual(names, ["datei.json"])

    def test_does_not_write_a_payload_that_cannot_be_serialised(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "datei.json"

            with self.assertRaises(TypeError):
                jsonio.write_json(path, {"objekt": object()})

            self.assertFalse(path.exists())
            self.assertEqual(os.listdir(folder), [])


class ReadSchemaJsonTest(unittest.TestCase):
    def test_returns_the_document_with_the_expected_schema(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "datei.json"
            jsonio.write_json(path, {"schema": "x/1", "wert": 3})

            data = jsonio.read_schema_json(path, "x/1", "Datei")

        self.assertEqual(data["wert"], 3)

    def test_names_what_is_missing_and_where(self):
        path = Path(tempfile.gettempdir()) / "gibt-es-nicht-4711.json"

        with self.assertRaises(FileNotFoundError) as caught:
            jsonio.read_schema_json(path, "x/1", "Tag-Map")

        self.assertEqual(str(caught.exception), f"Tag-Map nicht gefunden: {path}")

    def test_names_found_and_expected_schema(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "datei.json"
            jsonio.write_json(path, {"schema": "falsch/9"})

            with self.assertRaises(ValueError) as caught:
                jsonio.read_schema_json(path, "x/1", "Tag-Map")
            with self.assertRaises(ValueError) as named:
                jsonio.read_schema_json(path, "x/1", "Kalibrierung", schema_name="Kalibrierschema")

        self.assertEqual(
            str(caught.exception), f"Unbekanntes Tag-Map-Schema 'falsch/9' in {path} (erwartet x/1)"
        )
        self.assertIn("Unbekanntes Kalibrierschema 'falsch/9'", str(named.exception))

    def test_rejects_a_document_that_is_not_an_object(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "datei.json"
            path.write_text("[1, 2]", encoding="utf-8")

            with self.assertRaises(ValueError):
                jsonio.read_schema_json(path, "x/1", "Datei")


if __name__ == "__main__":
    unittest.main()
