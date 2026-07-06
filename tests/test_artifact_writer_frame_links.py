"""ArtifactWriter clean export must not invent a duplicate Key Frames appendix."""

from pathlib import Path

from src.application.services.artifact_writer import ArtifactWriter
from src.domain.types import PipelineRequest, OutputOptions


def test_clean_export_does_not_invent_key_frames_when_notes_have_no_links(tmp_path, monkeypatch):
    """Clean mode exports only visual evidence referenced by the note.

    This protects the V3/V13 policy: no redundant image-only Key Frames appendix
    is appended merely because extracted frames exist.
    """
    frame = tmp_path / "frame_0001.jpg"
    frame.write_bytes(b"jpg")

    monkeypatch.setattr(
        "src.application.services.artifact_writer.archive_to_obsidian",
        lambda *a, **k: None,
    )

    request = PipelineRequest(
        input="video.mp4",
        output_dir=str(tmp_path / "out"),
        title="Video",
        vault_path=None,
        output=OutputOptions(export_mode="clean"),
    )

    transcript_path, notes_path = ArtifactWriter.write(
        request,
        transcript="hello",
        notes="# Notes\n\nNo visual links.",
        segments=[],
        frames=[
            {
                "path": str(frame),
                "filename": "frame_0001.jpg",
                "timestamp_sec": 1.0,
            }
        ],
        job_id="00000000-0000-4000-8000-000000000001",
    )

    notes = Path(notes_path).read_text(encoding="utf-8")
    assert "Key Frames" not in notes
    assert "frames/frame_0001.jpg" not in notes
    assert not Path(notes_path).parent.joinpath("frames").exists()
    assert Path(transcript_path).is_file()


def test_empty_vault_path_does_not_archive(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "src.application.services.artifact_writer.archive_to_obsidian",
        lambda *args: calls.append(args),
    )

    request = PipelineRequest(
        input="video.mp4",
        output_dir=str(tmp_path / "out"),
        title="Video",
        vault_path="",
        output=OutputOptions(export_mode="clean"),
    )

    ArtifactWriter.write(
        request,
        transcript="hello",
        notes="# Notes\n",
        segments=[],
        frames=[],
        job_id="00000000-0000-4000-8000-000000000001",
    )

    assert calls == []


def test_configured_vault_path_archives_without_export_copy(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    output_dir = tmp_path / "out"
    request = PipelineRequest(
        input="video.mp4",
        output_dir=str(output_dir),
        title="Video",
        vault_path=f" {vault} ",
        output=OutputOptions(export_mode="clean"),
    )

    transcript_path, notes_path = ArtifactWriter.write(
        request,
        transcript="hello",
        notes="# Notes\n",
        segments=[],
        frames=[],
        job_id="00000000-0000-4000-8000-000000000001",
    )

    assert Path(notes_path) == vault / "video-notes" / "Video.md"
    assert Path(transcript_path) == vault / "video-notes" / "Video.transcript.txt"
    assert Path(notes_path).is_file()
    assert Path(transcript_path).is_file()
    assert not output_dir.exists()


def test_vault_archive_path_uses_obsidian_filename_rules(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    output_dir = tmp_path / "out"
    request = PipelineRequest(
        input="video.mp4",
        output_dir=str(output_dir),
        title="My Video: Part 1",
        vault_path=str(vault),
        output=OutputOptions(export_mode="clean"),
    )

    transcript_path, notes_path = ArtifactWriter.write(
        request,
        transcript="hello",
        notes="# Notes\n",
        segments=[],
        frames=[],
        job_id="00000000-0000-4000-8000-000000000001",
    )

    assert Path(notes_path) == vault / "video-notes" / "My_Video_Part_1.md"
    assert Path(transcript_path) == vault / "video-notes" / "My_Video_Part_1.transcript.txt"
    assert not output_dir.exists()
