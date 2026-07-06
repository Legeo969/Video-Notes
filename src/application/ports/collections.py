"""Collection repository port."""

from __future__ import annotations

from typing import Protocol


class CollectionRepositoryPort(Protocol):
    def insert_collection(
        self,
        collection_id: str,
        title: str,
        collection_type: str = "course",
        description: str | None = None,
        template_id: str | None = None,
        output_dir: str | None = None,
    ) -> None: ...

    def list_collections(self) -> list[dict]: ...
    def get_collection_by_id(self, collection_id: str) -> dict | None: ...
    def get_collection_by_title(self, title: str) -> dict | None: ...
    def delete_collection(self, collection_id: str) -> int: ...

    def insert_item(
        self,
        collection_id: str,
        job_id: str,
        item_index: int,
        title: str | None = None,
        source_uri: str | None = None,
        note_path: str | None = None,
        status: str | None = None,
        template_id: str | None = None,
    ) -> None: ...

    def update_item(
        self,
        collection_id: str,
        job_id: str,
        title: str | None = None,
        source_uri: str | None = None,
        note_path: str | None = None,
        status: str | None = None,
        template_id: str | None = None,
    ) -> None: ...

    def replace_item_job_id(
        self,
        collection_id: str,
        old_job_id: str,
        new_job_id: str,
        status: str | None = None,
    ) -> int: ...

    def get_item(self, collection_id: str, job_id: str) -> dict | None: ...
    def get_items(self, collection_id: str) -> list[dict]: ...
    def get_max_item_index(self, collection_id: str) -> int: ...

