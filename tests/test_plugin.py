"""Tests for plugin system (BasePlugin + PluginManager + integration).

Run with: python -m pytest tests/test_plugin.py -v
"""

import os
import sys
import argparse

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def plugin_dir(tmp_path):
    """Creates a temporary plugins/ root directory."""
    d = tmp_path / "plugins"
    d.mkdir(parents=True)
    return d


def _create_plugin(plugins_root, name, code):
    """Write a plugin file at plugins_root/name/plugin.py."""
    d = os.path.join(str(plugins_root), name)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "plugin.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPluginManagerDiscovery:
    """PluginManager 发现和加载行为."""

    def test_no_plugins_dir(self, tmp_path):
        """plugins 目录不存在，PluginManager 初始化不报错."""
        from src.application.plugin import PluginManager

        non_existent = str(tmp_path / "no_such_dir")
        pm = PluginManager(plugins_dir=non_existent)
        assert pm.plugins == []

    def test_load_single_plugin(self, plugin_dir):
        """创建临时 plugin.py，验证被加载."""
        _create_plugin(plugin_dir, "test_plugin", """
from src.application.plugin.base import BasePlugin

class TestPlugin(BasePlugin):
    name = "test"

plugin = TestPlugin()
""")
        from src.application.plugin import PluginManager

        pm = PluginManager(plugins_dir=str(plugin_dir))
        assert len(pm.plugins) == 1
        assert pm.plugins[0].name == "test"


class TestPluginHooks:
    """三种 hook 的正确调用."""

    def test_on_transcript_hook(self, plugin_dir):
        """插件修改 transcript，验证效果."""
        _create_plugin(plugin_dir, "modifier", """
from src.application.plugin.base import BasePlugin

class ModPlugin(BasePlugin):
    name = "modifier"
    def on_transcript(self, text):
        return text + " [processed]"

plugin = ModPlugin()
""")
        from src.application.plugin import PluginManager

        pm = PluginManager(plugins_dir=str(plugin_dir))
        results = pm.run_hook("on_transcript", "hello")
        assert len(results) == 1
        assert results[0] == "hello [processed]"

    def test_on_note_hook(self, plugin_dir):
        """插件修改笔记内容."""
        _create_plugin(plugin_dir, "note_mod", """
from src.application.plugin.base import BasePlugin

class NoteModPlugin(BasePlugin):
    name = "note_mod"
    def on_note(self, note_text, metadata):
        return note_text + "\\n\\n---\\n*processed*"

plugin = NoteModPlugin()
""")
        from src.application.plugin import PluginManager

        pm = PluginManager(plugins_dir=str(plugin_dir))
        results = pm.run_hook("on_note", "original note", {"title": "test"})
        assert len(results) == 1
        assert "*processed*" in results[0]

    def test_on_complete_hook(self, plugin_dir, tmp_path):
        """插件接收 on_complete 通知（通过副作用验证）. """
        marker = tmp_path / "completed.marker"

        _create_plugin(plugin_dir, "completer", r"""
from src.application.plugin.base import BasePlugin

class CompletePlugin(BasePlugin):
    name = "completer"
    def on_complete(self, note_path):
        with open(note_path, "w") as f:
            f.write("done")

plugin = CompletePlugin()
""")
        from src.application.plugin import PluginManager

        note_path = os.path.join(str(tmp_path), "test_note.md")
        pm = PluginManager(plugins_dir=str(plugin_dir))
        pm.run_hook("on_complete", note_path)

        assert os.path.isfile(note_path)
        with open(note_path) as f:
            assert f.read() == "done"

    def test_plugin_error_does_not_break(self, plugin_dir):
        """插件抛异常，主流程继续，异常被日志捕获."""
        _create_plugin(plugin_dir, "broken", """
from src.application.plugin.base import BasePlugin

class BrokenPlugin(BasePlugin):
    name = "broken"
    def on_transcript(self, text):
        raise ValueError("oops")

plugin = BrokenPlugin()
""")
        _create_plugin(plugin_dir, "good", """
from src.application.plugin.base import BasePlugin

class GoodPlugin(BasePlugin):
    name = "good"
    def on_transcript(self, text):
        return text + " OK"

plugin = GoodPlugin()
""")
        from src.application.plugin import PluginManager

        pm = PluginManager(plugins_dir=str(plugin_dir))
        # 有异常的插件不应中断整体流程
        results = pm.run_hook("on_transcript", "test")
        # broken 返回 None（异常被捕获），good 返回 "test OK"
        assert len(results) == 1
        assert results[0] == "test OK"

    def test_multiple_plugins(self, plugin_dir):
        """多个插件按目录名排序依次调用，外部链式应用结果."""
        _create_plugin(plugin_dir, "a_first", """
from src.application.plugin.base import BasePlugin

class APlugin(BasePlugin):
    name = "a_first"
    def on_transcript(self, text):
        return text + "[A]"

plugin = APlugin()
""")
        _create_plugin(plugin_dir, "b_second", """
from src.application.plugin.base import BasePlugin

class BPlugin(BasePlugin):
    name = "b_second"
    def on_transcript(self, text):
        return text + "[B]"

plugin = BPlugin()
""")
        from src.application.plugin import PluginManager

        pm = PluginManager(plugins_dir=str(plugin_dir))
        results = pm.run_hook("on_transcript", "start")

        # run_hook 传同样的原始参数给每个插件，不链式传递
        assert len(results) == 2
        assert results[0] == "start[A]"
        assert results[1] == "start[B]"


class TestPluginEnabled:
    """enabled 标志和禁用逻辑."""

    def test_enabled_flag(self, plugin_dir):
        """设置 enabled=False 的插件被跳过."""
        _create_plugin(plugin_dir, "disabled", """
from src.application.plugin.base import BasePlugin

class DisabledPlugin(BasePlugin):
    name = "disabled"
    enabled = False
    def on_transcript(self, text):
        return text + " [should not appear]"

plugin = DisabledPlugin()
""")
        _create_plugin(plugin_dir, "enabled", """
from src.application.plugin.base import BasePlugin

class EnabledPlugin(BasePlugin):
    name = "enabled"
    def on_transcript(self, text):
        return text + " [yes]"

plugin = EnabledPlugin()
""")
        from src.application.plugin import PluginManager

        pm = PluginManager(plugins_dir=str(plugin_dir))
        results = pm.run_hook("on_transcript", "test")
        assert len(results) == 1
        assert results[0] == "test [yes]"


class TestCLIIntegration:
    """CLI --no-plugins 集成."""

    def test_cli_no_plugins(self):
        """--no-plugins 参数使 plugin_manager 不被初始化."""
        parser = argparse.ArgumentParser()
        parser.add_argument("--no-plugins", action="store_true")

        # 有 --no-plugins → 跳过
        args = parser.parse_args(["--no-plugins"])
        pm = None
        if not args.no_plugins:
            from src.application.plugin import PluginManager
            pm = PluginManager()
        assert pm is None, "带有 --no-plugins 时不应初始化 PluginManager"

        # 无 --no-plugins → 正常加载（目录不存在只是空列表）
        args = parser.parse_args([])
        pm = None
        if not args.no_plugins:
            from src.application.plugin import PluginManager
            pm = PluginManager(plugins_dir="nonexistent_dir_for_test")
        assert pm is not None, "未设置 --no-plugins 时应初始化 PluginManager"
        assert pm.plugins == []
