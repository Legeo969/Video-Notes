"""Regression tests for visual recognition pipeline wiring."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch



import pytest

class TestVisionPipeline(unittest.TestCase):
    @pytest.mark.core
    def test_generate_notes_uses_explicit_summary_provider_settings(self):
        from src.application.notes import note_generator

        mock_provider = MagicMock()
        mock_provider.chat.return_value = "# Notes"

        with patch.object(note_generator, "get_provider", return_value=mock_provider) as mock_get:
            result = note_generator.generate_notes(
                "transcript",
                video_title="Video",
                provider="bailian",
                api_key="summary-key",
                base_url="https://summary.example.com/v1",
            )

        self.assertEqual(result, "# Notes")
        mock_get.assert_called_once_with(
            "bailian",
            api_key="summary-key",
            base_url="https://summary.example.com/v1",
        )

    @pytest.mark.core
    def test_note_prompt_includes_frame_visual_analysis(self):
        from src.application.llm.prompts import build_user_prompt

        prompt = build_user_prompt(
            "讲解了一个软件界面",
            "界面教程",
            frames=[
                {
                    "filename": "frame_0001.jpg",
                    "timestamp_sec": 12,
                    "analysis": "画面中展示了设置面板和 API Key 输入框。",
                }
            ],
        )

        self.assertIn("关键帧视觉识别", prompt)
        self.assertIn("画面中展示了设置面板", prompt)
        self.assertIn("![图片描述](frames/文件名)", prompt)

    @pytest.mark.core
    def test_note_prompt_includes_frame_ocr_text(self):
        from src.application.llm.prompts import build_user_prompt

        prompt = build_user_prompt(
            "讲解了一个软件界面",
            "界面教程",
            frames=[
                {
                    "filename": "frame_0001.jpg",
                    "timestamp_sec": 12,
                    "ocr_text": "API Key\nBase URL",
                }
            ],
        )

        self.assertIn("画面文字识别", prompt)
        self.assertIn("API Key", prompt)
        self.assertIn("Base URL", prompt)

    @pytest.mark.core
    def test_artifact_writer_appends_frame_links_to_notes(self):
        from src.application.services.artifact_writer import ArtifactWriter

        notes = "# 视频学习笔记\n\n## 内容\n\n笔记正文"
        frames = [
            {"filename": "frame_0001.jpg", "timestamp_sec": 1.0, "analysis": "UI 界面"},
            {"filename": "frame_0002.jpg", "timestamp_sec": 5.0, "ocr_text": "API Key"},
        ]

        result = ArtifactWriter._append_frame_links(notes, frames)

        self.assertIn("frame_0001.jpg", result)
        self.assertIn("frame_0002.jpg", result)
        self.assertIn("## 关键帧", result)

    @pytest.mark.core
    def test_artifact_writer_skips_frame_links_when_no_frames(self):
        from src.application.services.artifact_writer import ArtifactWriter

        notes = "# 纯音频笔记"
        result = ArtifactWriter._append_frame_links(notes, [])

        # 无 frames 时不变
        self.assertEqual(result, "# 纯音频笔记")


if __name__ == "__main__":
    unittest.main()
