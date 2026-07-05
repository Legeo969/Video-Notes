def test_default_model_dir_falls_back_to_home_faster_whisper(tmp_path, monkeypatch):
    from src.infrastructure.transcription import whisper_engine

    default_dir = tmp_path / "empty-default"
    home_dir = tmp_path / "faster-whisper"
    (home_dir / "faster-whisper-tiny").mkdir(parents=True)
    default_dir.mkdir()

    monkeypatch.delenv("WHISPER_MODEL_DIR", raising=False)
    monkeypatch.setattr(whisper_engine, "DEFAULT_MODEL_DIR", str(default_dir))
    monkeypatch.setattr(whisper_engine, "HOME_MODEL_DIR", str(home_dir), raising=False)

    assert whisper_engine.get_default_model_dir() == str(home_dir)


def test_resolve_model_checks_home_faster_whisper(tmp_path, monkeypatch):
    from src.infrastructure.transcription import whisper_engine

    default_dir = tmp_path / "empty-default"
    home_dir = tmp_path / "faster-whisper"
    model_path = home_dir / "faster-whisper-tiny"
    model_path.mkdir(parents=True)
    default_dir.mkdir()

    monkeypatch.delenv("WHISPER_MODEL_DIR", raising=False)
    monkeypatch.setattr(whisper_engine, "DEFAULT_MODEL_DIR", str(default_dir))
    monkeypatch.setattr(whisper_engine, "HOME_MODEL_DIR", str(home_dir), raising=False)

    assert whisper_engine._resolve_model("tiny") == str(model_path)


def test_resolve_model_accepts_direct_model_directory(tmp_path, monkeypatch):
    from src.infrastructure.transcription import whisper_engine

    model_root = tmp_path / "models"
    model_path = model_root / "medium"
    model_path.mkdir(parents=True)

    monkeypatch.delenv("WHISPER_MODEL_DIR", raising=False)
    monkeypatch.setattr(whisper_engine, "DEFAULT_MODEL_DIR", str(tmp_path / "empty-default"))
    monkeypatch.setattr(whisper_engine, "HOME_MODEL_DIR", str(tmp_path / "empty-home"), raising=False)

    assert whisper_engine._resolve_model("medium", str(model_root)) == str(model_path)
