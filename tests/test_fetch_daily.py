import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fetch_daily as daily


def payload(day="2026-11-12"):
    return {"data": [{"t": day, "o": 10, "h": 12, "l": 9, "c": 11, "v": 1000, "a": 5.5}]}


class DailyCacheTests(unittest.TestCase):
    def test_pack_preserves_volume_and_adjustment_and_sorts(self):
        data = payload()
        data["data"].extend(payload("2026-11-11")["data"])
        rows = daily.pack_rows(data)
        self.assertEqual(rows[0][0], "2026-11-11")
        self.assertEqual(rows[-1], ["2026-11-12", 10, 12, 9, 11, 1000, 5.5])

    def test_invalid_candles_are_rejected(self):
        for field, value in (("v", None), ("c", float("nan")), ("h", 8), ("l", 12)):
            data = payload()
            data["data"][0][field] = value
            self.assertEqual(daily.pack_rows(data), [])

    def test_fresh_cache_avoids_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            rows = daily.pack_rows(payload())
            daily.write_json(directory / "HTFL.json", {"symbol": "HTFL", "asOf": rows[-1][0], "fields": daily.FIELDS, "data": rows})
            with patch.object(daily, "fetch_history") as fetch:
                self.assertEqual(daily.refresh("HTFL", "2026-11-12", directory)[0], "cached")
                fetch.assert_not_called()

    def test_failed_or_regressed_refresh_preserves_previous_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path = directory / "HTFL.json"
            rows = daily.pack_rows(payload())
            daily.write_json(path, {"symbol": "HTFL", "asOf": rows[-1][0], "fields": daily.FIELDS, "data": rows})
            before = path.read_bytes()
            with patch.object(daily, "fetch_history", side_effect=RuntimeError("upstream down")):
                self.assertEqual(daily.refresh("HTFL", "2026-11-13", directory)[0], "retained")
            with patch.object(daily, "fetch_history", return_value=daily.pack_rows(payload("2026-11-11"))):
                self.assertEqual(daily.refresh("HTFL", "2026-11-13", directory)[0], "retained")
            self.assertEqual(path.read_bytes(), before)

    def test_dot_symbol_alias_retried_after_network_failure(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return json.dumps(payload()).encode()
        with patch.object(daily.urllib.request, "urlopen", side_effect=[OSError("offline")] * 3 + [Response()]) as fetch, patch.object(daily.time, "sleep"):
            self.assertTrue(daily.fetch_history("BRK.B"))
            self.assertIn("BRK-B", fetch.call_args.args[0].full_url)

    def test_empty_first_build_fails_without_ready_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "manifest.json").write_text('{"dates":["2026-11-13"]}')
            (directory / "2026-11-13.json").write_text('[{"s":"HTFL"}]')
            with patch.object(daily, "fetch_history", side_effect=RuntimeError("offline")):
                with self.assertRaises(RuntimeError):
                    daily.build(data_dir=directory)
            self.assertFalse((directory / "daily" / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
