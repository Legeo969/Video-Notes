from __future__ import annotations

from pathlib import Path

from src.api.handlers.collections import create_collections_handlers


def test_collection_handlers_keep_database_open_for_request(tmp_path: Path) -> None:
    """Regression: temporary context manager must not close DB before use."""
    output_dir = str(tmp_path / "notes")
    handlers = create_collections_handlers(output_dir=output_dir)

    assert handlers["collection.list"]({}) == []

    created = handlers["collection.create"]({
        "title": "Regression Collection",
        "collection_type": "course",
    })
    assert created["name"] == "Regression Collection"

    rows = handlers["collection.list"]({})
    assert len(rows) == 1
    assert rows[0]["id"] == created["id"]
    assert rows[0]["item_count"] == 0

    detail = handlers["collection.get"]({"collection_id": created["id"]})
    assert detail["name"] == "Regression Collection"
    assert detail["item_count"] == 0

    assert handlers["collection.list_items"]({
        "collection_id": created["id"],
    }) == []

    assert handlers["collection.delete"]({"collection_id": created["id"]}) is True
    assert handlers["collection.list"]({}) == []
