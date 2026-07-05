"""Regression tests for separating note structure from content detail."""

from __future__ import annotations

import inspect


def test_legacy_tutorial_migrates_to_coding_template():
    from src.application.notes.template_options import selection_from_settings

    assert selection_from_settings({"style": "教程"}) == (
        "coding_tutorial",
        "standard",
    )


def test_legacy_study_migrates_to_study_template():
    from src.application.notes.template_options import selection_from_settings

    assert selection_from_settings({"style": "学习笔记"}) == (
        "study",
        "standard",
    )


def test_new_settings_take_priority_over_legacy_style():
    from src.application.notes.template_options import selection_from_settings

    assert selection_from_settings(
        {
            "style": "教程",
            "template_id": "research",
            "detail_level": "detailed",
        }
    ) == ("research", "detailed")


def test_processing_state_keeps_template_and_detail_independent():
    from src.application.viewmodels.processing_form import ProcessingFormState

    state = ProcessingFormState(
        source_url="https://example.com/video",
        template_id="meeting",
        detail_level="concise",
    )
    request = state.to_pipeline_request()

    assert request.template_id == "meeting"
    assert request.style == "简洁"


def test_custom_template_file_overrides_builtin_template():
    from src.application.viewmodels.processing_form import ProcessingFormState

    state = ProcessingFormState(
        source_url="https://example.com/video",
        template="C:/templates/custom.md",
        template_id="research",
        detail_level="detailed",
    )
    request = state.to_pipeline_request()

    assert request.template == "C:/templates/custom.md"
    assert request.template_id is None
    assert request.style == "详细"


def test_pipeline_entrypoints_accept_template_id():
    from src.application.pipeline.video_pipeline import process_local, process_url

    assert "template_id" in inspect.signature(process_url).parameters
    assert "template_id" in inspect.signature(process_local).parameters


def test_template_prompt_receives_detail_guidance():
    from src.application.notes.prompt_builder import build_user_prompt
    from src.application.notes.template_loader import get_template_registry
    from src.domain.models.note_template import NoteContext

    template = get_template_registry().get("study")
    context = NoteContext(title="Test")

    concise = build_user_prompt(template, "transcript", context, style="简洁")
    detailed = build_user_prompt(template, "transcript", context, style="详细")

    assert "采用精简模式" in concise
    assert "采用详细模式" in detailed
    assert "模板章节" not in concise  # full prompt uses explicit section requirements
