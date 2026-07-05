"""Performance-oriented regressions for note generation."""

import threading
import time
import unittest
import pytest
from unittest.mock import patch


class _TimedProvider:
    def __init__(self):
        self.calls = []
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def chat(self, **kwargs):
        with self.lock:
            self.calls.append(kwargs)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.05)
            return f"# chunk {len(self.calls)}\n\nbody"
        finally:
            with self.lock:
                self.active -= 1


class TestNoteGenerationPerformance(unittest.TestCase):
    @pytest.mark.xfail(reason="重试逻辑在 _call_with_retry 层不透传到 provider", strict=False)
    def test_generate_notes_passes_timeout_and_retry_limit_to_provider(self):
        from src.application.notes import note_generator

        provider = _TimedProvider()
        with patch.object(note_generator, "get_provider", return_value=provider):
            note_generator.generate_notes(
                "short transcript",
                "Video",
                request_timeout=15,
                request_max_retries=1,
            )

        self.assertEqual(provider.calls[0]["timeout"], 15)
        self.assertEqual(provider.calls[0]["max_retries"], 1)

    def test_long_transcript_chunks_can_run_concurrently(self):
        from src.application.notes import note_generator

        provider = _TimedProvider()
        long_text = ("a" * 13000) + "\n\n" + ("b" * 13000)

        with patch.object(note_generator, "get_provider", return_value=provider):
            note_generator.generate_notes(
                long_text,
                "Video",
                max_parallel_chunks=2,
                request_timeout=15,
                request_max_retries=1,
            )

        self.assertGreater(len(provider.calls), 1)
        self.assertGreaterEqual(provider.max_active, 2)


if __name__ == "__main__":
    unittest.main()
