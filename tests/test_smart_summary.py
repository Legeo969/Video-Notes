"""Tests for V2 Chunk Summarize feature"""

import unittest
from unittest.mock import patch, MagicMock
import sys
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

    def test_processing_form_forwards_vision_and_ocr_options(self):
        """Processing form state forwards multimodal processing options."""
        from src.application.viewmodels.processing_form import ProcessingFormState

        state = ProcessingFormState(
            file_path="video.mp4",
            whisper_model="large-v3",
            output_dir="./output",
            ai_model="mimo-v2.5",
            vision_enabled=True,
            vision_provider="自定义",
            vision_model="vision-model",
            vision_api_key="vision-key",
            vision_base_url="https://vision.example.com/v1",
            ocr_enabled=True,
        )

        request = state.to_pipeline_request()

        self.assertTrue(request.vision_enabled)
        self.assertEqual(request.vision_provider, "自定义")
        self.assertEqual(request.vision_model, "vision-model")
        self.assertEqual(request.vision_api_key, "vision-key")
        self.assertEqual(request.vision_base_url, "https://vision.example.com/v1")
        self.assertTrue(request.ocr_enabled)


if __name__ == "__main__":
    unittest.main()
