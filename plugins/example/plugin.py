"""示例插件 — 在笔记末尾添加签名。

使用方法：将本目录放在项目根目录 plugins/ 下，自动加载。
"""

from src.application.plugin.base import BasePlugin


class ExamplePlugin(BasePlugin):
    name = "example"

    def on_note(self, note_text: str, metadata: dict) -> str | None:
        footer = "\n\n---\n*本笔记由 ExamplePlugin 处理*"
        if not note_text.endswith(footer):
            return note_text + footer
        return None


plugin = ExamplePlugin()
