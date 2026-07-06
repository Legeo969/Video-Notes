from __future__ import annotations

import json
import sys
from pathlib import Path

from src.utils.runtime_components import activate_runtime_components, installed_component_paths
from src.utils import external_tools


def test_runtime_component_activation_adds_installed_component_to_search_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = tmp_path / "runtime"
    installed = runtime / "components" / ".installed"
    component = runtime / "components" / "whisper-cpp-tools"
    installed.mkdir(parents=True)
    (component / "whisper-cli.exe").parent.mkdir(parents=True)
    (component / "whisper-cli.exe").write_text("exe", encoding="utf-8")
    (installed / "whisper-cpp-tools.json").write_text(
        json.dumps(
            {
                "component": "whisper-cpp-tools",
                "version": "1.0.0",
                "platform": "windows-x86_64",
                "engine_api": 1,
                "provides": ["transcription-native"],
                "files": ["whisper-cli.exe"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "path", sys.path.copy())

    paths = activate_runtime_components(
        components=["ffmpeg-tools", "whisper-cpp-tools"],
        provides="transcription-native",
        runtime_root=runtime,
    )

    assert paths == [component.resolve()]
    assert sys.path[0] == str(component.resolve())


def test_installed_component_paths_follow_requested_component_order(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    installed = runtime / "components" / ".installed"
    installed.mkdir(parents=True)
    for name in ("whisper-cpp-tools", "ffmpeg-tools"):
        (runtime / "components" / name).mkdir(parents=True)
        (installed / f"{name}.json").write_text(
            json.dumps(
                {
                    "component": name,
                    "version": "1.0.0",
                    "platform": "windows-x86_64",
                    "engine_api": 1,
                    "provides": ["native-tools"],
                }
            ),
            encoding="utf-8",
        )

    paths = installed_component_paths(
        components=["ffmpeg-tools", "whisper-cpp-tools"],
        provides="native-tools",
        runtime_root=runtime,
    )

    assert [path.name for path in paths] == ["ffmpeg-tools", "whisper-cpp-tools"]


def test_resolve_tool_checks_runtime_component_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = tmp_path / "runtime"
    installed = runtime / "components" / ".installed"
    component = runtime / "components" / "download-tools"
    installed.mkdir(parents=True)
    (component / "yt-dlp.exe").parent.mkdir(parents=True)
    (component / "yt-dlp.exe").write_text("exe", encoding="utf-8")
    (installed / "download-tools.json").write_text(
        json.dumps(
            {
                "component": "download-tools",
                "version": "1.0.0",
                "platform": "windows-x86_64",
                "engine_api": 1,
                "provides": ["download"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("VIDEO_NOTES_RUNTIME_DIR", str(runtime))
    monkeypatch.setattr(external_tools.shutil, "which", lambda _name: None)
    monkeypatch.setattr(external_tools, "verify_tool", lambda _command: True)

    resolved = external_tools.resolve_tool(
        "yt-dlp",
        components=["download-tools"],
        provides="download",
    )

    assert resolved == str((component / "yt-dlp.exe").resolve())
