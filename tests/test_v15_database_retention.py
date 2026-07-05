from __future__ import annotations

from pathlib import Path

from src.application.services.job_queue import JobQueue, get_default_db_path
from src.db.database import connect


def _queue(tmp_path: Path) -> JobQueue:
    output = tmp_path / "out"
    return JobQueue(get_default_db_path(str(output)), output_dir=str(output))


def _seed_index(queue: JobQueue, run_id: int, job_id: str, output: Path) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("# exported note\n\nkeep me", encoding="utf-8")
    with connect(queue.db_path) as conn:
        cursor = conn.execute(
            "INSERT INTO notes(rel_path,title,content) VALUES(?,?,?)",
            ("exports/note.md", "note", "# duplicate database content"),
        )
        note_id = int(cursor.lastrowid)
        conn.execute(
            "INSERT INTO note_keywords(note_id,keyword) VALUES(?,?)",
            (note_id, "cleanup-test"),
        )
        conn.execute(
            "UPDATE processing_runs SET note_id=?, output_path=? WHERE id=?",
            (note_id, str(output), run_id),
        )
        conn.execute(
            "INSERT INTO video_sources(job_id,source_type,source_uri) VALUES(?,?,?)",
            (job_id, "local", "video.mp4"),
        )
        conn.execute(
            "INSERT INTO transcript_segments(job_id,segment_index,start_time,end_time,text) "
            "VALUES(?,?,?,?,?)",
            (job_id, 0, 0, 1, "transcript"),
        )
        frame_id = conn.execute(
            "INSERT INTO frame_assets(job_id,frame_index,timestamp,path) VALUES(?,?,?,?)",
            (job_id, 0, 0.5, "frame.jpg"),
        ).lastrowid
        conn.execute(
            "INSERT INTO ocr_results(job_id,frame_id,timestamp,text) VALUES(?,?,?,?)",
            (job_id, frame_id, 0.5, "ocr"),
        )
        block_id = conn.execute(
            "INSERT INTO knowledge_blocks(note_id_int,job_id,block_index,content) "
            "VALUES(?,?,?,?)",
            (note_id, job_id, 0, "block"),
        ).lastrowid
        conn.execute(
            "INSERT INTO block_sources(block_id,source_kind,source_id) VALUES(?,?,?)",
            (block_id, "transcript", 1),
        )
        conn.commit()
    return note_id


def test_permanent_cleanup_removes_hidden_database_rows_but_keeps_exports(tmp_path: Path):
    queue = _queue(tmp_path)
    job_id = "11111111-1111-4111-8111-111111111111"
    run_id = queue.enqueue("video.mp4", job_id=job_id)
    queue.fail(run_id, "finished for test")
    workspace = Path(queue.get_job_dir(run_id))
    exported_note = tmp_path / "out" / "exports" / "note.md"
    note_id = _seed_index(queue, run_id, job_id, exported_note)

    assert queue.clear_all() == 1
    result = queue.purge_hidden_history()

    assert result["runs"] == 1
    assert result["workspaces"] == 1
    assert result["notes"] == 1
    assert not workspace.exists()
    assert exported_note.read_text(encoding="utf-8").startswith("# exported note")

    with connect(queue.db_path) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute(
            "SELECT COUNT(*) FROM processing_runs WHERE id=?", (run_id,)
        ).fetchone()[0] == 0
        for table in (
            "video_sources",
            "transcript_segments",
            "frame_assets",
            "ocr_results",
            "knowledge_blocks",
            "block_sources",
        ):
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM notes WHERE id=?", (note_id,)
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM note_keywords WHERE note_id=?", (note_id,)
        ).fetchone()[0] == 0


def test_permanent_cleanup_skips_jobs_used_by_collections(tmp_path: Path):
    queue = _queue(tmp_path)
    job_id = "22222222-2222-4222-8222-222222222222"
    run_id = queue.enqueue("collection-video.mp4", job_id=job_id)
    queue.fail(run_id, "finished for test")
    workspace = Path(queue.get_job_dir(run_id))

    with connect(queue.db_path) as conn:
        conn.execute(
            "INSERT INTO collections(collection_id,title,collection_type,created_at,updated_at) "
            "VALUES('collection-1','Collection','series',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "INSERT INTO collection_items(collection_id,job_id,item_index,status,created_at,updated_at) "
            "VALUES('collection-1',?,0,'completed',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
            (job_id,),
        )
        conn.execute(
            "INSERT INTO transcript_segments(job_id,segment_index,start_time,end_time,text) "
            "VALUES(?,?,?,?,?)",
            (job_id, 0, 0, 1, "needed by collection"),
        )
        conn.commit()

    assert queue.clear_all() == 1
    result = queue.purge_hidden_history()

    assert result["runs"] == 0
    assert result["collection_skipped"] == 1
    assert workspace.is_dir()
    with connect(queue.db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM processing_runs WHERE id=?", (run_id,)
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM transcript_segments WHERE job_id=?", (job_id,)
        ).fetchone()[0] == 1


def test_purge_keeps_note_still_referenced_by_another_run(tmp_path: Path):
    queue = _queue(tmp_path)
    hidden_id = queue.enqueue(
        "old.mp4", job_id="33333333-3333-4333-8333-333333333333"
    )
    visible_id = queue.enqueue(
        "new.mp4", job_id="44444444-4444-4444-8444-444444444444"
    )
    queue.fail(hidden_id, "old")
    queue.fail(visible_id, "new")

    with connect(queue.db_path) as conn:
        note_id = conn.execute(
            "INSERT INTO notes(rel_path,title,content) VALUES('shared.md','shared','content')"
        ).lastrowid
        conn.execute(
            "UPDATE processing_runs SET note_id=?, is_hidden=1 WHERE id=?",
            (note_id, hidden_id),
        )
        conn.execute(
            "UPDATE processing_runs SET note_id=?, is_hidden=0 WHERE id=?",
            (note_id, visible_id),
        )
        conn.commit()

    result = queue.purge_hidden_history()
    assert result["runs"] == 1
    assert result["notes"] == 0
    with connect(queue.db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM notes WHERE id=?", (note_id,)
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM processing_runs WHERE id=?", (visible_id,)
        ).fetchone()[0] == 1


def test_single_task_delete_does_not_leave_orphan_note_content(tmp_path: Path):
    queue = _queue(tmp_path)
    job_id = "55555555-5555-4555-8555-555555555555"
    run_id = queue.enqueue("delete-me.mp4", job_id=job_id)
    queue.fail(run_id, "finished")
    exported_note = tmp_path / "out" / "exports" / "single-delete.md"
    note_id = _seed_index(queue, run_id, job_id, exported_note)

    assert queue.delete_job(run_id) is True
    assert exported_note.is_file()
    with connect(queue.db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM notes WHERE id=?", (note_id,)
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM processing_runs WHERE id=?", (run_id,)
        ).fetchone()[0] == 0


def test_permanent_cleanup_sweeps_legacy_orphan_notes_even_without_hidden_jobs(tmp_path: Path):
    queue = _queue(tmp_path)
    with connect(queue.db_path) as conn:
        note_id = conn.execute(
            "INSERT INTO notes(rel_path,title,content) VALUES('legacy.md','legacy',?)",
            ("large legacy content" * 100,),
        ).lastrowid
        conn.commit()

    result = queue.purge_hidden_history()
    assert result["runs"] == 0
    assert result["notes"] == 1
    with connect(queue.db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM notes WHERE id=?", (note_id,)
        ).fetchone()[0] == 0


def test_orphan_note_path_used_by_collection_is_protected(tmp_path: Path):
    queue = _queue(tmp_path)
    with connect(queue.db_path) as conn:
        note_id = conn.execute(
            "INSERT INTO notes(rel_path,title,content) VALUES('exports/kept.md','kept','content')"
        ).lastrowid
        conn.execute(
            "INSERT INTO collections(collection_id,title,collection_type,created_at,updated_at) "
            "VALUES('collection-note','Collection','series',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "INSERT INTO collection_items(collection_id,job_id,item_index,note_path,status,created_at,updated_at) "
            "VALUES('collection-note','legacy-job',0,?,'completed',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
            (str(tmp_path / "out" / "exports" / "kept.md"),),
        )
        conn.commit()

    result = queue.purge_hidden_history()
    assert result["notes"] == 0
    with connect(queue.db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM notes WHERE id=?", (note_id,)
        ).fetchone()[0] == 1
