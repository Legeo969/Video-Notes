from __future__ import annotations

import json
from pathlib import Path

from src.application.pipeline.context import ProcessingContext
from src.application.pipeline.runner import FileManifestStore, StageRunner
from src.application.pipeline.stages.resolve_media import ResolveMediaStage
from src.application.pipeline.stages.transcribe_stage import TranscribeStage
from src.application.pipeline.stages.vision_stage import VisionStage
from src.application.vision.frame_understanding import FrameInsight
from src.application.speech import SpeechResult, SpeechSegment
from src.domain.types import PipelineRequest


class _MediaResolver:
    def __init__(self, audio: Path, video: Path):
        self.audio = audio
        self.video = video
        self.calls = 0

    def resolve(self, request, *, job_dir):
        self.calls += 1
        return str(self.audio), str(self.video), []


class _Transcriber:
    def __init__(self):
        self.calls = 0

    def transcribe(self, audio_path, *, language=None, beam_size=5, vad_filter=False):
        self.calls += 1
        return SpeechResult(
            segments=[SpeechSegment(0.0, 1.0, "hello", language="en")],
            full_text="hello",
            language="en",
            elapsed=0.1,
        )


def _workspace(tmp_path: Path):
    job_dir = tmp_path / ".jobs" / "resume-job"
    (job_dir / "artifacts").mkdir(parents=True)
    (job_dir / "temp").mkdir(parents=True)
    source = tmp_path / "video.mp4"
    source.write_bytes(b"video")
    extracted = tmp_path / "fresh-audio.wav"
    extracted.write_bytes(b"audio")
    return job_dir, source, extracted


def test_unrelated_llm_setting_change_does_not_invalidate_media_or_whisper(tmp_path: Path):
    job_dir, source, extracted = _workspace(tmp_path)
    media = _MediaResolver(extracted, source)
    transcriber = _Transcriber()
    stages = [ResolveMediaStage(media), TranscribeStage(transcriber)]
    store = FileManifestStore()

    first_request = PipelineRequest(
        input=str(source),
        output_dir=str(tmp_path),
        api_key="old-key",
        gpt_model="old-model",
    )
    first_ctx = ProcessingContext(first_request, str(job_dir), job_dir.name)
    StageRunner(store).run(first_ctx, stages)
    assert media.calls == 1
    assert transcriber.calls == 1

    # API key/model affect only note generation. They must not trigger media or
    # Whisper again, even without the tolerant explicit-resume path.
    second_request = PipelineRequest(
        input=str(source),
        output_dir=str(tmp_path),
        api_key="new-key",
        gpt_model="new-model",
    )
    second_ctx = ProcessingContext(second_request, str(job_dir), job_dir.name)
    restored = StageRunner(store).run(second_ctx, stages)

    assert media.calls == 1
    assert transcriber.calls == 1
    assert restored["speech_result"].full_text == "hello"


def test_resume_accepts_intact_legacy_manifest_even_when_old_hash_differs(tmp_path: Path):
    job_dir, source, extracted = _workspace(tmp_path)
    artifact_audio = job_dir / "artifacts" / "audio.wav"
    artifact_audio.write_bytes(b"cached-audio")
    transcript_json = job_dir / "artifacts" / "transcript.json"
    transcript_json.write_text(
        json.dumps({"text": "cached", "segments": [{"start": 0, "end": 1, "text": "cached"}], "language": "en"}),
        encoding="utf-8",
    )
    manifest_dir = job_dir / "artifacts" / "_manifest"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "resolve_media.json").write_text(
        json.dumps({
            "stage": "resolve_media",
            "status": "completed",
            "artifact_files": [],
            "input_hash": "v17-full-request-hash-that-no-longer-matches",
            "outputs": {"audio_path": str(artifact_audio), "video_path": str(source)},
        }),
        encoding="utf-8",
    )
    (manifest_dir / "transcribe.json").write_text(
        json.dumps({
            "stage": "transcribe",
            "status": "completed",
            "artifact_files": [],
            "input_hash": "another-v17-hash",
            "outputs": {
                "speech_result": {
                    "segments": [{"start": 0, "end": 1, "text": "cached", "language": "en"}],
                    "full_text": "cached",
                    "language": "en",
                    "elapsed": 1.0,
                }
            },
        }),
        encoding="utf-8",
    )

    media = _MediaResolver(extracted, source)
    transcriber = _Transcriber()
    request = PipelineRequest(
        input=str(source),
        output_dir=str(tmp_path),
        api_key="key-added-after-failure",
    )
    ctx = ProcessingContext(
        request,
        str(job_dir),
        job_dir.name,
        resume_run_id=7,
    )
    result = StageRunner(FileManifestStore()).run(
        ctx,
        [ResolveMediaStage(media), TranscribeStage(transcriber)],
    )

    assert media.calls == 0
    assert transcriber.calls == 0
    assert result["speech_result"].full_text == "cached"


def test_resume_reruns_media_when_cached_video_path_is_missing(tmp_path: Path):
    job_dir, source, extracted = _workspace(tmp_path)
    artifact_audio = job_dir / "artifacts" / "audio.wav"
    artifact_audio.write_bytes(b"cached-audio")
    missing_video = tmp_path / "missing.mp4"
    manifest_dir = job_dir / "artifacts" / "_manifest"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "resolve_media.json").write_text(
        json.dumps({
            "stage": "resolve_media",
            "status": "completed",
            "artifact_files": [],
            "input_hash": "old-hash",
            "outputs": {"audio_path": str(artifact_audio), "video_path": str(missing_video)},
        }),
        encoding="utf-8",
    )

    media = _MediaResolver(extracted, source)
    request = PipelineRequest(input=str(source), output_dir=str(tmp_path))
    ctx = ProcessingContext(request, str(job_dir), job_dir.name, resume_run_id=9)
    result = StageRunner(FileManifestStore()).run(ctx, [ResolveMediaStage(media)])

    assert media.calls == 1
    assert result["video_path"] == str(source)


def test_resume_restores_vision_insights_as_domain_objects(tmp_path: Path):
    job_dir, source, _ = _workspace(tmp_path)
    frame = job_dir / "temp" / "frame.jpg"
    frame.write_bytes(b"frame")
    manifest_dir = job_dir / "artifacts" / "_manifest"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "vision_analysis.json").write_text(
        json.dumps({
            "stage": "vision_analysis",
            "status": "completed",
            "artifact_files": [],
            "input_hash": "legacy-hash",
            "outputs": {
                "insights": [{
                    "timestamp": 12.5,
                    "image_path": str(frame),
                    "visual_summary": "diagram",
                    "visual_importance": "important",
                    "importance_score": 0.9,
                    "related_topic": "topic",
                    "transcript_relation": "matches",
                    "chapter": "Part 1",
                }]
            },
        }),
        encoding="utf-8",
    )

    request = PipelineRequest(input=str(source), output_dir=str(tmp_path), vision_enabled=True)
    ctx = ProcessingContext(request, str(job_dir), job_dir.name, resume_run_id=11)
    result = StageRunner(FileManifestStore()).run(
        ctx,
        [VisionStage(vision_provider=object())],
        initial_state={"frames": [], "speech_result": None},
    )

    assert isinstance(result["insights"][0], FrameInsight)
    assert result["insights"][0].timestamp == 12.5
