import os
import sys
import argparse

from src.app.cli.registry import CliCommand


def _get_job_collections(db_path, job_id):
    import sqlite3
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT c.title, c.collection_id
               FROM collection_items ci
               JOIN collections c ON ci.collection_id = c.collection_id
               WHERE ci.job_id = ?
               ORDER BY c.title""",
            (job_id,),
        ).fetchall()
        conn.close()
        return [f"{r['title']} ({r['collection_id']})" for r in rows]
    except Exception:
        return []


def _cmd_collection_create(title, collection_type, template_id, output_dir):
    from src.application.services.collection_manager import CollectionManager

    manager = CollectionManager(output_dir=output_dir)
    result = manager.create(title, collection_type=collection_type, template_id=template_id)
    print(f"✅ 集合已创建: {result['title']} (ID: {result['collection_id']})")


def _cmd_collection_list(output_dir):
    from src.application.services.collection_manager import CollectionManager

    manager = CollectionManager(output_dir=output_dir)
    collections = manager.list_collections()
    if not collections:
        print("📭 暂无集合")
        return
    print(f"\n📋 集合列表 ({len(collections)} 个):\n")
    for c in collections:
        print(f"  {c['collection_id']:<24} {c['title']} ({c.get('type', '?')})")
        print(f"  {'':>24} {c.get('description', '')}")
        print()


def _cmd_collection_status(identifier, output_dir):
    from src.application.services.collection_manager import CollectionManager

    manager = CollectionManager(output_dir=output_dir)
    status = manager.get_status(identifier)
    if not status:
        print(f"❌ 未找到集合: {identifier}")
        return
    print(f"\n📊 集合状态: {status['title']}\n")
    print(f"  ID:         {status['collection_id']}")
    print(f"  类型:       {status.get('type', '?')}")
    print(f"  任务数:     {status['total_jobs']}")
    print(f"  已完成:     {status['completed_jobs']}")
    print(f"  完成率:     {status['completion_rate']:.0%}")
    print()


def _cmd_collection_add_job(collection_id, run_id, output_dir):
    from src.application.services.collection_manager import CollectionManager

    manager = CollectionManager(output_dir=output_dir)
    result = manager.add_job(collection_id, run_id)
    print(f"✅ 任务 #{run_id} 已加入集合 {result['collection_id']}")


def _cmd_collection_overview(identifier, output_dir):
    from src.application.services.collection_manager import CollectionManager

    manager = CollectionManager(output_dir=output_dir)
    overview = manager.generate_overview(identifier)
    if overview is None:
        print(f"❌ 未找到集合: {identifier}")
        return
    print(overview)


def _cmd_folder_import(folder_path, collection_id, collection_type, template_id, output_dir, recursive, sort):
    from src.application.services.collection_manager import CollectionManager

    manager = CollectionManager(output_dir=output_dir)
    result = manager.import_folder(
        folder_path,
        collection_id=collection_id,
        collection_type=collection_type,
        template_id=template_id,
        recursive=recursive,
        sort=sort,
    )
    print(f"✅ 从文件夹导入完成: {result['count']} 个文件")


def _cmd_playlist_import(playlist_url, collection_id, collection_type, template_id, output_dir):
    from src.application.services.collection_manager import CollectionManager

    manager = CollectionManager(output_dir=output_dir)
    result = manager.import_playlist(
        playlist_url,
        collection_id=collection_id,
        collection_type=collection_type,
        template_id=template_id,
    )
    print(f"✅ 从播放列表导入完成: {result['count']} 个视频")


def _cmd_collection_export(identifier, output_dir):
    from src.application.services.collection_manager import CollectionManager

    manager = CollectionManager(output_dir=output_dir)
    result = manager.export(identifier)
    print(f"✅ 集合已导出到: {result['export_path']}")


class CollectionCreateCommand:
    name = "collection-create"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        pass

    def matches(self, args: argparse.Namespace) -> bool:
        return getattr(args, "collection_create", None) is not None

    def run(self, args: argparse.Namespace) -> int:
        _cmd_collection_create(
            args.collection_create, args.collection_type,
            args.template, args.output,
        )
        return 0


class CollectionListCommand:
    name = "collection-list"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        pass

    def matches(self, args: argparse.Namespace) -> bool:
        return getattr(args, "collection_list", False)

    def run(self, args: argparse.Namespace) -> int:
        _cmd_collection_list(args.output)
        return 0


class CollectionStatusCommand:
    name = "collection-status"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        pass

    def matches(self, args: argparse.Namespace) -> bool:
        return getattr(args, "collection_status", None) is not None

    def run(self, args: argparse.Namespace) -> int:
        _cmd_collection_status(args.collection_status, args.output)
        return 0


class CollectionAddJobCommand:
    name = "collection-add-job"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        pass

    def matches(self, args: argparse.Namespace) -> bool:
        return getattr(args, "collection_add_job", None) is not None

    def run(self, args: argparse.Namespace) -> int:
        cid, run_id_str = args.collection_add_job
        _cmd_collection_add_job(cid, int(run_id_str), args.output)
        return 0


class CollectionOverviewCommand:
    name = "collection-overview"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        pass

    def matches(self, args: argparse.Namespace) -> bool:
        return getattr(args, "collection_overview", None) is not None

    def run(self, args: argparse.Namespace) -> int:
        _cmd_collection_overview(args.collection_overview, args.output)
        return 0


class FolderImportCommand:
    name = "folder-import"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        pass

    def matches(self, args: argparse.Namespace) -> bool:
        return getattr(args, "folder_path", None) is not None

    def run(self, args: argparse.Namespace) -> int:
        _cmd_folder_import(
            args.folder_path, args.collection_id, args.collection_type,
            args.template, args.output, args.recursive, args.sort,
        )
        return 0


class PlaylistImportCommand:
    name = "playlist-import"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        pass

    def matches(self, args: argparse.Namespace) -> bool:
        return getattr(args, "playlist_url", None) is not None

    def run(self, args: argparse.Namespace) -> int:
        _cmd_playlist_import(
            args.playlist_url, args.collection_id, args.collection_type,
            args.template, args.output,
        )
        return 0


class CollectionExportCommand:
    name = "collection-export"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        pass

    def matches(self, args: argparse.Namespace) -> bool:
        return getattr(args, "collection_export", None) is not None

    def run(self, args: argparse.Namespace) -> int:
        _cmd_collection_export(args.collection_export, args.output)
        return 0


def register_collections(registry):
    registry.register(CollectionCreateCommand())
    registry.register(CollectionListCommand())
    registry.register(CollectionStatusCommand())
    registry.register(CollectionAddJobCommand())
    registry.register(CollectionOverviewCommand())
    registry.register(FolderImportCommand())
    registry.register(PlaylistImportCommand())
    registry.register(CollectionExportCommand())
