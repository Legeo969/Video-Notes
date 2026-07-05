"""Tests for durable settings writes."""

import json
import os
import base64
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestSettingsAtomicWrite(unittest.TestCase):
    def test_update_settings_replaces_file_atomically(self):
        from src.config.settings import update_settings

        real_replace = os.replace
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            settings_path.write_text(
                json.dumps({"old": "value"}, ensure_ascii=False),
                encoding="utf-8",
            )

            with patch("src.config.settings.os.replace") as mock_replace:
                mock_replace.side_effect = real_replace
                update_settings(
                    {"new": "value"},
                    str(settings_path),
                    remove_keys=["old"],
                )

            mock_replace.assert_called_once()
            tmp_path, final_path = mock_replace.call_args.args
            self.assertNotEqual(tmp_path, str(settings_path))
            self.assertEqual(final_path, str(settings_path))

            data = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(data, {"new": "value"})


class TestSettingsSecrets(unittest.TestCase):
    def test_decode_legacy_secret_keeps_raw_key(self):
        from src.config.settings import decode_legacy_secret

        raw_key = "raw-key-that-happens-to-look-normal"
        self.assertEqual(decode_legacy_secret(raw_key), raw_key)

    def test_decode_legacy_secret_keeps_base64_like_non_sk_key(self):
        from src.config.settings import decode_legacy_secret

        raw_key = base64.b64encode(b"not-an-old-sk-key").decode("ascii")
        self.assertEqual(decode_legacy_secret(raw_key), raw_key)

    def test_decode_legacy_secret_decodes_old_sk_base64(self):
        from src.config.settings import decode_legacy_secret

        encoded = base64.b64encode(b"sk-old-secret").decode("ascii")
        self.assertEqual(decode_legacy_secret(encoded), "sk-old-secret")


if __name__ == "__main__":
    unittest.main()
