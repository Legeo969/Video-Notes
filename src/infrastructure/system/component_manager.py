"""Component Manager — 运行时组件安装、更新、回滚。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ComponentManifest:
    """组件清单。"""
    component: str
    version: str
    platform: str
    engine_api: int
    description: str = ""
    sha256: str = ""
    signature: str = ""
    size_mb: int = 0
    requires: dict[str, str] = field(default_factory=dict)
    provides: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)


class ComponentManager:
    """管理运行时组件的安装、更新、回滚。"""

    def __init__(self, base_dir: str | Path) -> None:
        self._base_dir = Path(base_dir)
        self._components_dir = self._base_dir / "components"
        self._manifests_dir = self._base_dir / "manifests"
        self._components_dir.mkdir(parents=True, exist_ok=True)
        self._manifests_dir.mkdir(parents=True, exist_ok=True)

    def list_components(self) -> list[ComponentManifest]:
        """列出所有已安装的组件。"""
        result: list[ComponentManifest] = []
        for f in self._manifests_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text("utf-8"))
                result.append(ComponentManifest(**data))
            except Exception as e:
                logger.warning("Invalid manifest %s: %s", f.name, e)
        return result

    def get_component(self, name: str) -> ComponentManifest | None:
        """获取指定组件的最新清单。"""
        for comp in self.list_components():
            if comp.component == name:
                return comp
        return None

    def install_component(self, manifest: ComponentManifest, source_dir: Path) -> bool:
        """从 source_dir 安装组件到 components/ 目录。"""
        target = self._components_dir / f"{manifest.component}-{manifest.version}"

        if target.exists():
            logger.info("Component %s version %s already installed", manifest.component, manifest.version)
            return True

        # 复制文件
        shutil.copytree(source_dir, target)

        # 写入清单
        manifest_path = self._manifests_dir / f"{manifest.component}.json"
        manifest_path.write_text(
            json.dumps(manifest.__dict__, ensure_ascii=False, indent=2),
            "utf-8",
        )

        # 创建 current 符号链接
        current_link = self._components_dir / manifest.component
        if current_link.exists() or current_link.is_symlink():
            current_link.unlink()
        try:
            os.symlink(target.name, current_link, target_is_directory=True)
        except OSError:
            # Windows 可能不支持符号链接，使用 junction 或副本
            logger.warning("Cannot create symlink on this platform")

        logger.info("Installed %s version %s", manifest.component, manifest.version)
        return True

    def remove_component(self, name: str) -> bool:
        """删除组件。"""
        current_link = self._components_dir / name
        if current_link.exists():
            current_link.unlink()

        manifest_path = self._manifests_dir / f"{name}.json"
        if manifest_path.exists():
            manifest_path.unlink()

        # 删除所有版本
        for d in self._components_dir.glob(f"{name}-*"):
            if d.is_dir():
                shutil.rmtree(d)

        logger.info("Removed component %s", name)
        return True

    def verify_component(self, name: str) -> dict[str, Any]:
        """验证组件完整性。"""
        manifest = self.get_component(name)
        if not manifest:
            return {"status": "not_found", "name": name}

        current_link = self._components_dir / name
        if not current_link.exists():
            return {"status": "not_installed", "name": name}

        resolved = current_link.resolve()
        if not resolved.exists():
            return {"status": "broken", "name": name}

        # 校验 SHA-256
        if manifest.sha256:
            hasher = hashlib.sha256()
            for root, _dirs, files in os.walk(resolved):
                for f in sorted(files):
                    filepath = Path(root) / f
                    hasher.update(filepath.read_bytes())
            if hasher.hexdigest() != manifest.sha256:
                return {"status": "hash_mismatch", "name": name}

        return {"status": "ok", "name": name, "version": manifest.version}