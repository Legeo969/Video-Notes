"""插件系统核心：BasePlugin + PluginManager。"""

import importlib.util
import logging
import os

logger = logging.getLogger(__name__)


class BasePlugin:
    """插件基类，所有插件须继承此类。

    三个 hook 点：
    - on_transcript(text) → str | None
    - on_note(note_text, metadata) → str | None
    - on_complete(note_path) → None
    """

    name: str = "unnamed"
    enabled: bool = True

    def on_transcript(self, text: str) -> str | None:
        return None

    def on_note(self, note_text: str, metadata: dict) -> str | None:
        return None

    def on_complete(self, note_path: str) -> None:
        pass


class PluginManager:
    """扫描 plugins/ 目录，动态加载插件，提供 hook 调用。"""

    def __init__(self, plugins_dir: str | None = None):
        self._plugins_dir = plugins_dir or os.path.join(os.getcwd(), "plugins")
        self._plugins: list[BasePlugin] = []
        self._discover()

    # ── 发现与加载 ──────────────────────────────────────────────

    def _discover(self):
        if not os.path.isdir(self._plugins_dir):
            logger.debug("Plugins directory not found: %s", self._plugins_dir)
            return
        for entry in sorted(os.listdir(self._plugins_dir)):
            plugin_path = os.path.join(self._plugins_dir, entry, "plugin.py")
            if os.path.isfile(plugin_path):
                self._load_plugin(plugin_path)

    def _load_plugin(self, path: str):
        try:
            name = os.path.basename(os.path.dirname(path))
            spec = importlib.util.spec_from_file_location(f"plugin_{name}", path)
            if not spec or not spec.loader:
                logger.warning("Failed to load plugin: %s", path)
                return
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, "plugin"):
                self._plugins.append(mod.plugin)
                logger.info("Loaded plugin: %s (%s)", mod.plugin.name, path)
        except Exception:
            logger.exception("Error loading plugin: %s", path)

    # ── 公开接口 ────────────────────────────────────────────────

    @property
    def plugins(self) -> list[BasePlugin]:
        return list(self._plugins)

    def run_hook(self, name: str, *args, **kwargs) -> list:
        """运行所有已启用插件的指定 hook，返回结果列表。"""
        results: list = []
        for p in self._plugins:
            if not p.enabled:
                continue
            try:
                handler = getattr(p, name, None)
                if handler:
                    results.append(handler(*args, **kwargs))
            except Exception:
                logger.exception("Plugin %s hook %s failed", p.name, name)
        return results
