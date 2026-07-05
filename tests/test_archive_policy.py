"""Tests for ArchivePolicy dataclass and ObsidianArchiver class."""

import sys
import types

try:
    __import__("openai")
except ImportError:
    openai_stub = types.ModuleType("openai")
    class _OpenAIError(Exception):
        status_code = None
    openai_stub.OpenAI = object
    openai_stub.AuthenticationError = _OpenAIError
    openai_stub.APITimeoutError = _OpenAIError
    openai_stub.APIStatusError = _OpenAIError
    openai_stub.APIConnectionError = _OpenAIError
    sys.modules["openai"] = openai_stub

if "ctranslate2" not in sys.modules:
    ctranslate2_stub = types.ModuleType("ctranslate2")
    ctranslate2_stub.get_cuda_device_count = lambda: 0
    sys.modules["ctranslate2"] = ctranslate2_stub

if "faster_whisper" not in sys.modules:
    faster_whisper_stub = types.ModuleType("faster_whisper")
    faster_whisper_stub.WhisperModel = object
    sys.modules["faster_whisper"] = faster_whisper_stub

if "yt_dlp" not in sys.modules:
    yt_dlp_stub = types.ModuleType("yt_dlp")
    class _YoutubeDL:
        def __init__(self, *_args, **_kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def download(self, _urls): return None
    yt_dlp_stub.YoutubeDL = _YoutubeDL
    sys.modules["yt_dlp"] = yt_dlp_stub

if "PIL" not in sys.modules:
    pil_stub = types.ModuleType("PIL")
    image_stub = types.ModuleType("PIL.Image")
    image_stub.Image = object
    image_stub.open = lambda *_args, **_kwargs: object()
    pil_stub.Image = image_stub
    sys.modules["PIL"] = pil_stub
    sys.modules["PIL.Image"] = image_stub


import dataclasses
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


class TestArchivePolicy:
    """Tests for ArchivePolicy frozen dataclass."""

    def test_default_values(self):
        from src.infrastructure.artifacts.archive_policy import ArchivePolicy

        policy = ArchivePolicy()
        assert policy.copy_only_referenced_frames is True
        assert policy.normalize_obsidian_links is True
        assert policy.include_frontmatter is True

    def test_frozen_dataclass(self):
        from src.infrastructure.artifacts.archive_policy import ArchivePolicy

        policy = ArchivePolicy()
        with pytest.raises(dataclasses.FrozenInstanceError):
            policy.copy_only_referenced_frames = False

    def test_custom_values(self):
        from src.infrastructure.artifacts.archive_policy import ArchivePolicy

        policy = ArchivePolicy(
            copy_only_referenced_frames=False,
            normalize_obsidian_links=False,
            include_frontmatter=False,
        )
        assert policy.copy_only_referenced_frames is False
        assert policy.normalize_obsidian_links is False
        assert policy.include_frontmatter is False

    def test_positional_args_disabled(self):
        """Frozen dataclass with kw_only means no positional args."""
        from src.infrastructure.artifacts.archive_policy import ArchivePolicy

        with pytest.raises(TypeError):
            ArchivePolicy(False, True, True)


class TestObsidianArchiver:
    """Tests for ObsidianArchiver class."""

    def test_init_stores_policy(self):
        from src.infrastructure.artifacts.archive_policy import ArchivePolicy
        from src.infrastructure.artifacts.obsidian import ObsidianArchiver

        policy = ArchivePolicy()
        archiver = ObsidianArchiver(policy)
        assert archiver.policy is policy

    def test_init_default_policy(self):
        from src.infrastructure.artifacts.archive_policy import ArchivePolicy
        from src.infrastructure.artifacts.obsidian import ObsidianArchiver

        archiver = ObsidianArchiver()
        assert isinstance(archiver.policy, ArchivePolicy)
        assert archiver.policy.copy_only_referenced_frames is True

    def test_archive_returns_bool(self):
        from src.infrastructure.artifacts.archive_policy import ArchivePolicy
        from src.infrastructure.artifacts.obsidian import ObsidianArchiver

        archiver = ObsidianArchiver(ArchivePolicy())
        # Expect False when vault doesn't exist (same as vault_writer behavior)
        result = archiver.archive("/nonexistent/notes.md", "/nonexistent/vault", "Test")
        assert result is False

    def test_archive_method_signature(self):
        """archive() has the required 3 params (notes_path, vault_path, video_title)."""
        from src.infrastructure.artifacts.obsidian import ObsidianArchiver
        import inspect

        sig = inspect.signature(ObsidianArchiver.archive)
        # Should have self + 3 args at minimum (possibly more with defaults)
        params = list(sig.parameters.keys())
        assert "self" in params
        assert "notes_path" in params
        assert "vault_path" in params
        assert "video_title" in params

    def test_archive_delegates_referenced_frames_only(self):
        """Referenced frames are copied; unreferenced frames are not."""
        from src.infrastructure.artifacts.archive_policy import ArchivePolicy
        from src.infrastructure.artifacts.obsidian import ObsidianArchiver

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_note_dir = root / "output" / "Video"
            frames_dir = output_note_dir / "frames"
            vault = root / "vault"
            frames_dir.mkdir(parents=True)
            vault.mkdir()

            note = output_note_dir / "Video.md"
            note.write_text(
                "# Video\n\n![example](frames/frame_0001.jpg)\n",
                encoding="utf-8",
            )
            (frames_dir / "frame_0001.jpg").write_bytes(b"image")
            (frames_dir / "frame_0002.jpg").write_bytes(b"unused")

            archiver = ObsidianArchiver(ArchivePolicy())
            with patch("builtins.print"):
                result = archiver.archive(str(note), str(vault), "Video")

            assert result is True
            archived_frames = vault / "video-notes" / "frames"
            assert (archived_frames / "frame_0001.jpg").is_file()
            assert not (archived_frames / "frame_0002.jpg").exists()

    def test_archive_copies_angle_link_frame_with_parentheses(self):
        from src.infrastructure.artifacts.archive_policy import ArchivePolicy
        from src.infrastructure.artifacts.obsidian import ObsidianArchiver

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_note_dir = root / "output" / "Video"
            frames_dir = output_note_dir / "frames"
            vault = root / "vault"
            frames_dir.mkdir(parents=True)
            vault.mkdir()

            frame_name = "frame_Demo (Part 1)_0001.jpg"
            note = output_note_dir / "Video.md"
            note.write_text(
                f"# Video\n\n![frame](<frames/{frame_name}>)\n",
                encoding="utf-8",
            )
            (frames_dir / frame_name).write_bytes(b"image")

            archiver = ObsidianArchiver(ArchivePolicy())
            with patch("builtins.print"):
                result = archiver.archive(str(note), str(vault), "Video")

            assert result is True
            archived_note = vault / "video-notes" / "Video.md"
            archived_content = archived_note.read_text(encoding="utf-8")
            assert f"![frame](<frames/{frame_name}>)" in archived_content
            assert (vault / "video-notes" / "frames" / frame_name).is_file()

    def test_archive_with_vault_writer_same_signature(self):
        """archive() has the same 3 required params as archive_to_obsidian()."""
        from src.infrastructure.artifacts.obsidian import ObsidianArchiver
        from src.vault_writer import archive_to_obsidian
        import inspect

        archiver_sig = inspect.signature(ObsidianArchiver.archive)
        vw_sig = inspect.signature(archive_to_obsidian)

        archiver_params = {k: v for k, v in archiver_sig.parameters.items() if k != "self"}
        vw_params = dict(vw_sig.parameters)

        # archive_to_obsidian(notes_path, vault_path, video_title)
        assert set(archiver_params.keys()) == set(vw_params.keys())
