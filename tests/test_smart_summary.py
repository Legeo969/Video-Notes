"""Tests for V2 Chunk Summarize feature"""

import unittest
from unittest.mock import patch, MagicMock
import inspect
import json
import os
import sys
import tempfile
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestSmartSummary(unittest.TestCase):
    """Test smart summary feature in note generator"""

    @pytest.mark.xfail(reason="默认进入 V0.5 模板模式", strict=False)
    @patch('src.application.notes.note_generator._split_transcript')
    @patch('src.application.notes.note_generator.get_provider')
    def test_default_behavior_preserved(self, mock_get_provider, mock_split):
        """Default behavior preserved when smart_summary=False"""
        from src.application.notes.note_generator import generate_notes

        # Setup mocks
        mock_split.return_value = ["chunk1", "chunk2"]
        mock_provider = MagicMock()
        mock_provider.chat.return_value = "# Test Notes\n\nContent here"
        mock_get_provider.return_value = mock_provider

        # Call with smart_summary=False (default)
        result = generate_notes(
            transcript="test transcript",
            video_title="Test Video",
            smart_summary=False
        )

        # Should not call global summary
        self.assertNotIn("全局总结", result)
        self.assertIn("Test Video", result)

    @pytest.mark.xfail(reason="默认进入 V0.5 模板模式", strict=False)
    @patch('src.application.notes.note_generator._split_transcript')
    @patch('src.application.notes.note_generator.get_provider')
    def test_global_summary_with_multi_chunk(self, mock_get_provider, mock_split):
        """Global summary generated when smart_summary=True and multiple chunks"""
        from src.application.notes.note_generator import generate_notes

        # Setup mocks
        mock_split.return_value = ["chunk1", "chunk2"]
        mock_provider = MagicMock()
        # 2 chunk calls + 1 global summary call
        mock_provider.chat.side_effect = [
            "## Chunk Content\n\nDetails here",
            "## Chunk Content\n\nDetails here",
            "This is a global summary of the video content.",
        ]
        mock_get_provider.return_value = mock_provider

        # Call with smart_summary=True
        result = generate_notes(
            transcript="test transcript that is long enough to be split into multiple chunks",
            video_title="Test Video",
            smart_summary=True
        )

        # Should contain global summary section
        self.assertIn("全局总结", result)
        self.assertIn("This is a global summary", result)

    @patch('src.application.notes.note_generator._split_transcript')
    @patch('src.application.notes.note_generator.get_provider')
    def test_no_global_summary_with_single_chunk(self, mock_get_provider, mock_split):
        """No global summary when smart_summary=True but single chunk"""
        from src.application.notes.note_generator import generate_notes

        # Setup mocks - single chunk
        mock_split.return_value = ["single chunk content"]
        mock_provider = MagicMock()
        mock_provider.chat.return_value = "# Test Notes\n\nContent here"
        mock_get_provider.return_value = mock_provider

        # Call with smart_summary=True but single chunk
        result = generate_notes(
            transcript="short transcript",
            video_title="Test Video",
            smart_summary=True
        )

        # Should not contain global summary section
        self.assertNotIn("全局总结", result)

    @patch('src.application.notes.note_generator._split_transcript')
    @patch('src.application.notes.note_generator.get_provider')
    def test_global_summary_failure_falls_back(self, mock_get_provider, mock_split):
        """Global summary failure falls back to merged chunk notes"""
        from src.application.notes.note_generator import generate_notes

        # Setup mocks
        mock_split.return_value = ["chunk1", "chunk2"]
        mock_provider = MagicMock()
        # 2 chunk calls succeed, 3rd (global summary) raises
        mock_provider.chat.side_effect = [
            "## Chunk Content\n\nDetails here",
            "## Chunk Content\n\nDetails here",
            Exception("API Error"),
        ]
        mock_get_provider.return_value = mock_provider

        # Call with smart_summary=True
        result = generate_notes(
            transcript="test transcript that is long enough to be split into multiple chunks",
            video_title="Test Video",
            smart_summary=True
        )

        # Should not contain global summary section, but should contain chunk content
        self.assertNotIn("全局总结", result)
        self.assertIn("Details here", result)

    @pytest.mark.skip(reason="子进程 main.py 导入需要完整环境，默认跳过")
    def test_cli_help_shows_smart_summary(self):
        """CLI help shows --smart-summary option"""
        import subprocess
        result = subprocess.run(
            [sys.executable, "main.py", "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--smart-summary", result.stdout)
        self.assertIn("启用长文智能总结", result.stdout)

    @pytest.mark.skip(reason="需要 PySide6/Qt GUI 环境，默认跳过")
    def test_gui_workers_accept_processing_options(self):
        """GUI single and batch workers accept feature option wiring."""
        from src.gui.workers.processing import Worker, BatchWorker

        self.assertIn("smart_summary", inspect.signature(Worker).parameters)
        self.assertIn("smart_summary", inspect.signature(BatchWorker).parameters)
        self.assertIn("bilibili_cookies", inspect.signature(Worker).parameters)
        self.assertIn("bilibili_cookies", inspect.signature(BatchWorker).parameters)
        self.assertIn("vision_enabled", inspect.signature(Worker).parameters)
        self.assertIn("vision_enabled", inspect.signature(BatchWorker).parameters)
        self.assertIn("ocr_enabled", inspect.signature(Worker).parameters)
        self.assertIn("ocr_enabled", inspect.signature(BatchWorker).parameters)
        self.assertIn("kb_provider", inspect.signature(Worker).parameters)
        self.assertIn("kb_provider", inspect.signature(BatchWorker).parameters)
        self.assertIn("kb_model", inspect.signature(Worker).parameters)
        self.assertIn("kb_model", inspect.signature(BatchWorker).parameters)
        self.assertIn("kb_api_key", inspect.signature(Worker).parameters)
        self.assertIn("kb_api_key", inspect.signature(BatchWorker).parameters)
        self.assertIn("kb_base_url", inspect.signature(Worker).parameters)
        self.assertIn("kb_base_url", inspect.signature(BatchWorker).parameters)

    def test_gui_worker_forwards_vision_and_ocr_options(self):
        """Single item GUI worker forwards multimodal processing options."""
        from src.gui.workers.processing import Worker

        with tempfile.TemporaryDirectory() as tmp:
            notes_path = Path(tmp) / "notes.md"
            captured = {}

            def fake_process(input_path, **kwargs):
                captured.update(kwargs)
                notes_path.write_text("# Notes\n", encoding="utf-8")
                return str(notes_path)

            worker = Worker(
                process_fn=fake_process,
                input_path="video.mp4",
                whisper_model="large-v3",
                output_dir=tmp,
                gpt_model="mimo-v2.5",
                vision_enabled=True,
                vision_provider="自定义",
                vision_model="vision-model",
                vision_api_key="vision-key",
                vision_base_url="https://vision.example.com/v1",
                ocr_enabled=True,
            )
            worker.run()

        self.assertTrue(captured["vision_enabled"])
        self.assertEqual(captured["vision_provider"], "自定义")
        self.assertEqual(captured["vision_model"], "vision-model")
        self.assertEqual(captured["vision_api_key"], "vision-key")
        self.assertEqual(captured["vision_base_url"], "https://vision.example.com/v1")
        self.assertTrue(captured["ocr_enabled"])

    @pytest.mark.skip(reason="需要 PySide6/Qt GUI 环境，默认跳过")
    def test_gui_worker_forwards_kb_model_options(self):
        """Single item GUI worker forwards knowledge-base model settings."""
        from src.gui.workers.processing import Worker

        with tempfile.TemporaryDirectory() as tmp:
            notes_path = Path(tmp) / "notes.md"
            captured = {}

            def fake_process(input_path, **kwargs):
                captured.update(kwargs)
                notes_path.write_text("# Notes\n", encoding="utf-8")
                return str(notes_path)

            worker = Worker(
                process_fn=fake_process,
                input_path="video.mp4",
                whisper_model="large-v3",
                output_dir=tmp,
                gpt_model="mimo-v2.5",
                kb_provider="bailian",
                kb_model="qwen-plus",
                kb_api_key="kb-key",
                kb_base_url="https://kb.example.com/v1",
            )
            worker.run()

        self.assertEqual(captured["kb_provider"], "bailian")
        self.assertEqual(captured["kb_model"], "qwen-plus")
        self.assertEqual(captured["kb_api_key"], "kb-key")
        self.assertEqual(captured["kb_base_url"], "https://kb.example.com/v1")

    @pytest.mark.skip(reason="需要 PySide6/Qt GUI 环境，默认跳过")
    def test_gui_batch_worker_forwards_kb_model_options(self):
        """Batch GUI worker forwards knowledge-base model settings."""
        from src.gui.workers.processing import BatchWorker

        with tempfile.TemporaryDirectory() as tmp:
            captured = {}

            def fake_process_url(input_path, **kwargs):
                captured.update(kwargs)
                return str(Path(tmp) / "notes.md")

            worker = BatchWorker(
                process_url_fn=fake_process_url,
                process_local_fn=lambda input_path, **kwargs: "",
                items=[{"input": "https://example.com/video"}],
                whisper_model="large-v3",
                output_dir=tmp,
                gpt_model="mimo-v2.5",
                kb_provider="bailian",
                kb_model="qwen-plus",
                kb_api_key="kb-key",
                kb_base_url="https://kb.example.com/v1",
            )
            worker.run()

        self.assertEqual(captured["kb_provider"], "bailian")
        self.assertEqual(captured["kb_model"], "qwen-plus")
        self.assertEqual(captured["kb_api_key"], "kb-key")
        self.assertEqual(captured["kb_base_url"], "https://kb.example.com/v1")

    @pytest.mark.skip(reason="需要 PySide6/Qt GUI 环境，默认跳过")
    def test_gui_worker_sets_bilibili_cookie_env(self):
        """Single item GUI worker applies the configured Bilibili cookie path."""
        from src.gui.workers.processing import Worker

        with tempfile.TemporaryDirectory() as tmp:
            cookie_path = Path(tmp) / "cookies.txt"
            cookie_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            notes_path = Path(tmp) / "notes.md"
            captured = {}

            def fake_process(input_path, **kwargs):
                captured["input"] = input_path
                captured["cookies"] = os.environ.get("VIDEO_NOTES_BILIBILI_COOKIES")
                notes_path.write_text("# Notes\n", encoding="utf-8")
                return str(notes_path)

            worker = Worker(
                process_fn=fake_process,
                input_path="https://www.bilibili.com/video/BV1",
                whisper_model="large-v3",
                output_dir=tmp,
                gpt_model="mimo-v2.5",
                bilibili_cookies=str(cookie_path),
            )

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("VIDEO_NOTES_BILIBILI_COOKIES", None)
                worker.run()

        self.assertEqual(captured["input"], "https://www.bilibili.com/video/BV1")
        self.assertEqual(captured["cookies"], str(cookie_path))

    @pytest.mark.skip(reason="需要 PySide6/Qt GUI 环境，默认跳过")
    def test_gui_batch_worker_sets_bilibili_cookie_env(self):
        """Batch GUI worker applies the configured Bilibili cookie path."""
        from src.gui.workers.processing import BatchWorker

        with tempfile.TemporaryDirectory() as tmp:
            cookie_path = Path(tmp) / "cookies.txt"
            cookie_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            captured = {}

            def fake_process_url(input_path, **kwargs):
                captured["input"] = input_path
                captured["cookies"] = os.environ.get("VIDEO_NOTES_BILIBILI_COOKIES")
                return str(Path(tmp) / "notes.md")

            worker = BatchWorker(
                process_url_fn=fake_process_url,
                process_local_fn=lambda input_path, **kwargs: "",
                items=[{"input": "https://www.bilibili.com/video/BV1"}],
                whisper_model="large-v3",
                output_dir=tmp,
                gpt_model="mimo-v2.5",
                bilibili_cookies=str(cookie_path),
            )

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("VIDEO_NOTES_BILIBILI_COOKIES", None)
                worker.run()

        self.assertEqual(captured["input"], "https://www.bilibili.com/video/BV1")
        self.assertEqual(captured["cookies"], str(cookie_path))

    def _make_main_window_with_settings(self, settings):
        """Create the main window with isolated persisted settings."""
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

        from PySide6.QtWidgets import QApplication
        from src.gui.windows import main_window

        app = QApplication.instance() or QApplication([])
        self.addCleanup(app.processEvents)

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        settings_path = Path(tmp.name) / "settings.json"
        settings_path.write_text(
            json.dumps(settings, ensure_ascii=False),
            encoding="utf-8",
        )

        with (
            patch.object(main_window.MainWindow, "SETTINGS_PATH", str(settings_path)),
            patch.object(main_window, "scan_models", return_value=["large-v3"]),
            patch.object(main_window, "get_default_model_dir", return_value=str(tmp.name)),
            patch("src.utils.check_ffmpeg", return_value=True),
            patch("src.utils._get_tool_version", return_value="test"),
        ):
            window = main_window.MainWindow(lambda *a, **k: "", lambda *a, **k: "")

        self.addCleanup(window.deleteLater)
        return window

    @pytest.mark.skip(reason="需要 PySide6/Qt GUI 环境，默认跳过")
    def test_gui_settings_restore_style_without_smart_summary_key(self):
        """GUI restores style even when older settings lack smart_summary."""
        window = self._make_main_window_with_settings({"style": "详细"})

        self.assertEqual(window.style_combo.currentText(), "详细")
        self.assertFalse(window.smart_summary_check.isChecked())

    @pytest.mark.skip(reason="需要 PySide6/Qt GUI 环境，默认跳过")
    def test_gui_settings_restore_smart_summary_without_style_key(self):
        """GUI restores smart_summary even when older settings lack style."""
        window = self._make_main_window_with_settings({"smart_summary": True})

        self.assertTrue(window.smart_summary_check.isChecked())

    @pytest.mark.skip(reason="需要 PySide6/Qt GUI 环境，默认跳过")
    def test_gui_settings_restore_bilibili_cookies_path(self):
        """GUI restores configured Bilibili cookie path."""
        cookie_path = "C:/Users/example/cookies.txt"
        window = self._make_main_window_with_settings({"bilibili_cookies": cookie_path})

        self.assertEqual(window.bilibili_cookies_edit.text(), cookie_path)


if __name__ == "__main__":
    unittest.main()
