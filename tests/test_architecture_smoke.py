import importlib
import inspect
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import pytest

pytestmark = pytest.mark.skip(reason="需要完整环境（typing_extensions + 无超长环境变量），默认跳过")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class ArchitectureSmokeTests(unittest.TestCase):
    def test_cli_help_still_exposes_existing_options(self):
        result = subprocess.run(
            [sys.executable, "main.py", "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--batch", result.stdout)
        self.assertIn("--subtitle-format", result.stdout)
        self.assertIn("--obsidian-vault", result.stdout)
        self.assertIn("--frame-mode", result.stdout)
        self.assertIn("--max-frames", result.stdout)
        self.assertIn("--bilibili-cookies", result.stdout)
        self.assertIn("--ocr", result.stdout)
        self.assertIn("--check-ocr", result.stdout)

    def test_cli_check_ocr_reports_runtime_versions(self):
        result = subprocess.run(
            [sys.executable, "main.py", "--check-ocr"],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PaddleOCR", result.stdout)
        self.assertIn("PaddlePaddle", result.stdout)
        self.assertIn("PaddleOCR pipeline: OK", result.stdout)

    def test_cli_bilibili_cookies_argument_sets_env_for_processing(self):
        cli = importlib.import_module("src.app.cli")

        with tempfile.TemporaryDirectory() as tmp:
            cookie_path = Path(tmp) / "cookies.txt"
            cookie_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            captured = {}

            def fake_process_url(url, **kwargs):
                captured["url"] = url
                captured["cookies"] = os.environ.get("VIDEO_NOTES_BILIBILI_COOKIES")
                return str(Path(tmp) / "notes.md")

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("VIDEO_NOTES_BILIBILI_COOKIES", None)
                with (
                    patch.object(sys, "argv", [
                        "main.py",
                        "https://www.bilibili.com/video/BV1",
                        "--bilibili-cookies",
                        str(cookie_path),
                    ]),
                    patch.object(cli, "process_url", fake_process_url),
                ):
                    cli.main()

        self.assertEqual(captured["url"], "https://www.bilibili.com/video/BV1")
        self.assertEqual(captured["cookies"], str(cookie_path))

    def test_rebuild_index_cli_handles_gbk_console_encoding(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "output"
            note_dir = output_dir / "Video A"
            note_dir.mkdir(parents=True)
            (note_dir / "Video A.md").write_text("# Video A\n\nRAG notes\n", encoding="utf-8")

            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "gbk:strict"
            result = subprocess.run(
                [sys.executable, "main.py", "--rebuild-index", "--output", str(output_dir)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=30,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("UnicodeEncodeError", result.stderr)
            self.assertTrue((output_dir / ".note_index" / "entries.json").exists())

    def test_main_and_pipeline_entrypoint_signatures_match(self):
        import main
        pipeline = importlib.import_module("src.application.pipeline.video_pipeline")

        self.assertEqual(
            inspect.signature(main.process_url),
            inspect.signature(pipeline.process_url),
        )
        self.assertEqual(
            inspect.signature(main.process_local),
            inspect.signature(pipeline.process_local),
        )
        self.assertTrue(callable(main.main))

    def test_legacy_subtitle_writer_reexports_core_writer(self):
        legacy = importlib.import_module("src.subtitle_writer")
        core = importlib.import_module("src.infrastructure.transcription.subtitle_writer")

        self.assertIs(legacy.write_srt, core.write_srt)
        self.assertIs(legacy.write_ass, core.write_ass)
        self.assertIs(legacy.write_timestamped_txt, core.write_timestamped_txt)

    def test_subtitle_writer_outputs_are_stable(self):
        writer = importlib.import_module("src.infrastructure.transcription.subtitle_writer")
        segments = [
            {"start": 0.0, "end": 1.25, "text": "你好"},
            {"start": 1.25, "end": 3.0, "text": "世界"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            srt_path = tmp_path / "transcript.srt"
            txt_path = tmp_path / "transcript_timestamped.txt"
            writer.write_srt(segments, str(srt_path))
            writer.write_timestamped_txt(segments, str(txt_path))

            self.assertIn("00:00:00,000 --> 00:00:01,250", srt_path.read_text(encoding="utf-8"))
            self.assertIn("[00:00:00.000] → [00:00:01.250] 你好", txt_path.read_text(encoding="utf-8"))

    def test_utils_package_preserves_safe_dirname(self):
        utils = importlib.import_module("src.utils")

        self.assertEqual(utils._safe_dirname('a/b:c*?'), "abc")

    def test_sqlite_database_boundary_initializes(self):
        database = importlib.import_module("src.db.database")

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "video_notes.db"
            database.initialize_database(str(db_path))
            self.assertTrue(db_path.exists())

    def test_pyinstaller_spec_references_runtime_boundaries(self):
        spec = (ROOT / "video-notes-ai.spec").read_text(encoding="utf-8")

        self.assertIn("main.py", spec)
        self.assertIn("src", spec)
        self.assertIn("cuda_runtime_hook.py", spec)

    def test_pyinstaller_spec_collects_paddlex_ocr_core_metadata(self):
        spec = (ROOT / "video-notes-ai.spec").read_text(encoding="utf-8")

        self.assertIn("copy_metadata", spec)
        for package in [
            "imagesize",
            "opencv-contrib-python",
            "pyclipper",
            "pypdfium2",
            "python-bidi",
            "shapely",
        ]:
            self.assertIn(package, spec)

    def test_bilibili_cookie_env_path_is_preferred(self):
        compat = importlib.import_module("src.infrastructure.video.yt_dlp_compat")

        with tempfile.TemporaryDirectory() as tmp:
            cookie_path = Path(tmp) / "cookies.txt"
            cookie_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")

            with patch.dict(
                os.environ,
                {"VIDEO_NOTES_BILIBILI_COOKIES": str(cookie_path)},
            ):
                paths = compat._candidate_cookie_paths()

        self.assertEqual(paths[0], cookie_path)

    def test_bilibili_cookie_loader_keeps_session_cookies(self):
        compat = importlib.import_module("src.infrastructure.video.yt_dlp_compat")
        bilibili = importlib.import_module("yt_dlp.extractor.bilibili")

        with tempfile.TemporaryDirectory() as tmp:
            cookie_path = Path(tmp) / "cookies.txt"
            cookie_path.write_text(
                "# Netscape HTTP Cookie File\n"
                ".bilibili.com\tTRUE\t/\tFALSE\t0\tSESSDATA\tlogged-in\n",
                encoding="utf-8",
            )

            loaded = {}

            class FakeExtractor:
                def _set_cookie(self, domain, name, value):
                    loaded[(domain, name)] = value

            with patch.dict(
                os.environ,
                {"VIDEO_NOTES_BILIBILI_COOKIES": str(cookie_path)},
            ):
                compat.apply_yt_dlp_compat("https://www.bilibili.com/video/BV1")
                base = bilibili.BilibiliBaseIE
                if hasattr(base, "_video_notes_ai_bili_cookies_loaded"):
                    delattr(base, "_video_notes_ai_bili_cookies_loaded")
                base._load_bili_cookies(FakeExtractor())

        self.assertEqual(loaded[(".bilibili.com", "SESSDATA")], "logged-in")


if __name__ == "__main__":
    unittest.main()
