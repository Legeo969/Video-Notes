from __future__ import annotations

from pathlib import Path

from src.api.handlers.notes import create_notes_handlers
from src.infrastructure.db.gateway import DatabaseGateway
from src.infrastructure.db.repositories.note_repository import NoteRepository


def test_notes_handlers_keep_database_open_for_request(tmp_path: Path) -> None:
    output_dir = tmp_path / "notes"
    db_path = output_dir / ".note_index" / "video_notes.db"
    gateway = DatabaseGateway(str(db_path))
    gateway.initialize()
    with gateway.connection() as conn:
        note_id = NoteRepository(conn).upsert(
            rel_path="demo.md",
            title="Demo note",
            content="# Demo",
        )

    handlers = create_notes_handlers(db_path=str(db_path), output_dir=str(output_dir))
    rows = handlers["notes.list"]({})
    assert len(rows) == 1
    assert rows[0]["id"] == note_id

    detail = handlers["notes.get"]({"note_id": note_id})
    assert detail["title"] == "Demo note"
    assert detail["content"] == "# Demo"

    detail_by_id = handlers["notes.get"]({"id": note_id})
    assert detail_by_id["title"] == "Demo note"
    assert detail_by_id["content"] == "# Demo"

    by_path = handlers["notes.get_by_path"]({"path": "demo.md"})
    assert by_path["id"] == note_id
