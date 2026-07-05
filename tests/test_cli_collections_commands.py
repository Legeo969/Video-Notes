"""Tests for CLI collection commands — smoke tests for _cmd_collection_*."""

from __future__ import annotations

import os
from pathlib import Path

from src.app.cli.commands.collections import (
    _cmd_collection_create,
    _cmd_collection_list,
    _cmd_collection_status,
)


def test_collection_cli_create_list_status(tmp_path: Path, capsys) -> None:
    """Happy path: create → list → status via CLI commands."""
    output_dir = str(tmp_path / "out")

    _cmd_collection_create("Course One", "course", "study", output_dir)
    _cmd_collection_list(output_dir)
    _cmd_collection_status("course-one", output_dir)

    out = capsys.readouterr().out
    assert "Course One" in out
    assert "course-one" in out
    assert "完成率" in out


def test_collection_cli_create_duplicate(tmp_path: Path) -> None:
    """Creating a duplicate collection raises ValueError."""
    output_dir = str(tmp_path / "out2")
    _cmd_collection_create("Dup", "course", None, output_dir)

    import pytest
    with pytest.raises(ValueError, match="dup"):
        _cmd_collection_create("Dup", "course", None, output_dir)


def test_collection_cli_status_unknown(tmp_path: Path, capsys) -> None:
    """Status for a non-existent collection should print error, not crash."""
    output_dir = str(tmp_path / "out3")
    _cmd_collection_status("nonexistent-slug", output_dir)
    out = capsys.readouterr().out
    assert "未找到集合" in out or "❌" in out


def test_collection_cli_list_empty(tmp_path: Path, capsys) -> None:
    """Listing collections when none exist should not crash."""
    output_dir = str(tmp_path / "out4")
    _cmd_collection_list(output_dir)
    out = capsys.readouterr().out
    assert "暂无集合" in out


def test_collection_manager_import_folder_no_media(tmp_path: Path) -> None:
    """import_folder with empty folder returns 0 items."""
    from src.application.services.collection_manager import CollectionManager

    output_dir = str(tmp_path / "out5")
    empty_folder = tmp_path / "empty_folder"
    empty_folder.mkdir()

    mgr = CollectionManager(output_dir=output_dir)
    result = mgr.import_folder(str(empty_folder))
    assert result["count"] == 0
    assert "collection_id" in result


def test_collection_manager_export_nonexistent(tmp_path: Path) -> None:
    """export on non-existent collection raises ValueError."""
    from src.application.services.collection_manager import CollectionManager

    output_dir = str(tmp_path / "out6")
    mgr = CollectionManager(output_dir=output_dir)
    import pytest
    with pytest.raises(ValueError, match="集合不存在"):
        mgr.export("nope")


def test_collection_manager_add_job_nonexistent(tmp_path: Path) -> None:
    """add_job with invalid run_id raises ValueError."""
    from src.application.services.collection_manager import CollectionManager

    output_dir = str(tmp_path / "out7")
    mgr = CollectionManager(output_dir=output_dir)
    import pytest
    with pytest.raises(ValueError, match="任务不存在"):
        mgr.add_job("some-collection", 9999)
