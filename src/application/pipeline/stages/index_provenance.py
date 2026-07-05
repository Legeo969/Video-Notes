"""IndexProvenanceStage — persist note, blocks and evidence provenance."""

from __future__ import annotations

import hashlib
import logging
import os
import re
from typing import Any

from src.application.pipeline.context import ProcessingContext
from src.application.pipeline.stages.base import StageResult

logger = logging.getLogger(__name__)


def _markdown_blocks(markdown: str) -> list[tuple[str, str]]:
    """Split a note into stable H2 blocks for provenance linking."""
    matches = list(re.finditer(r"(?m)^##\s+(.+?)\s*$", markdown or ""))
    if not matches:
        text = (markdown or "").strip()
        return [("完整笔记", text)] if text else []
    blocks: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        title = match.group(1).strip()
        content = markdown[match.end():end].strip()
        if content:
            blocks.append((title, content))
    return blocks


class IndexProvenanceStage:
    """Persist source, transcript, final frames, OCR and note blocks."""

    id = "index_provenance"
    label = "Provenance 索引"
    percent = 97

    def __init__(self, provenance_indexer_cls=None):
        self._indexer_cls = provenance_indexer_cls

    @staticmethod
    def cache_inputs(ctx: ProcessingContext, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "notes_path": state.get("notes_path"),
            "notes": state.get("notes", ""),
            "frames": state.get("frames", []),
            "output_dir": ctx.request.output_dir,
            "title": ctx.request.title,
            "source": ctx.request.input,
            "job_id": ctx.job_id,
        }

    def run(self, ctx: ProcessingContext, state: dict[str, Any]) -> StageResult:
        note_id = None
        try:
            if self._indexer_cls is None:
                from src.application.provenance import ProvenanceIndexer
                indexer_cls = ProvenanceIndexer
            else:
                indexer_cls = self._indexer_cls

            from src.db.database import connect, upsert_note

            prov_db = os.path.join(ctx.request.output_dir, ".note_index", "video_notes.db")
            notes_path = str(state.get("notes_path") or "")
            notes = str(state.get("notes") or "")
            if notes_path and os.path.isfile(notes_path):
                if not notes:
                    with open(notes_path, "r", encoding="utf-8") as f:
                        notes = f.read()
                rel_path = os.path.relpath(notes_path, ctx.request.output_dir).replace("\\", "/")
                note_id = upsert_note(
                    prov_db,
                    rel_path=rel_path,
                    title=ctx.request.title or os.path.basename(notes_path),
                    content=notes,
                )

                # Rebuild deterministic note blocks for this job.
                conn = connect(prov_db)
                try:
                    conn.execute(
                        "DELETE FROM block_sources WHERE block_id IN "
                        "(SELECT id FROM knowledge_blocks WHERE job_id = ?)",
                        (ctx.job_id,),
                    )
                    conn.execute("DELETE FROM knowledge_blocks WHERE job_id = ?", (ctx.job_id,))
                    for block_index, (title, content) in enumerate(_markdown_blocks(notes)):
                        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                        conn.execute(
                            """INSERT INTO knowledge_blocks
                               (note_id, note_id_int, job_id, block_index, block_type,
                                title, content, summary, confidence, content_hash)
                               VALUES (?, ?, ?, ?, 'section', ?, ?, ?, 1.0, ?)""",
                            (rel_path, note_id, ctx.job_id, block_index,
                             title, content, content[:240], digest),
                        )
                    conn.commit()
                finally:
                    conn.close()

            source_type = "url" if ctx.request.input.startswith("http") else "local"
            indexer = indexer_cls(prov_db)
            index_kwargs = dict(
                job_dir=ctx.job_dir,
                source_type=source_type,
                source_uri=ctx.request.input,
                title=ctx.request.title,
            )
            if "frames" in state:
                index_kwargs["frames"] = state.get("frames") or []
            result = indexer.index_job(ctx.job_id, **index_kwargs)
            if not result.success:
                logger.warning("Provenance 索引未完整完成: %s", "; ".join(result.warnings))
            else:
                logger.info(
                    "Provenance 已写入: segments=%d frames=%d ocr=%d blocks=%d sources=%d",
                    result.segments_count,
                    result.frames_count,
                    result.ocr_count,
                    result.blocks_count,
                    result.sources_count,
                )
        except Exception as e:
            logger.warning("Provenance 索引失败（非致命）: %s", e)

        return StageResult(outputs={"note_id": note_id})
