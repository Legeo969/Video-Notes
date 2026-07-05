"""图片处理器 — 编码 + 多模态分析。

支持将常见图片格式编码为 base64 data URI，
通过 vision-capable LLM 进行内容分析并生成笔记。
"""

import base64
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 支持的图片扩展名
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}

# 扩展名 -> MIME 映射
MIME_MAP = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}

VISION_PROMPT = """请详细分析这张图片的内容，包括：
1. 画面中的主要元素和物体
2. 场景和背景描述
3. 颜色、构图和风格分析
4. 图片中的文字内容（如果有）
5. 整体给人的感觉

以清晰的笔记格式输出分析结果。"""


def is_image(path: str) -> bool:
    """检查文件扩展名是否为支持的图片格式。

    Args:
        path: 文件路径

    Returns:
        扩展名在 IMAGE_EXTENSIONS 中返回 True
    """
    return Path(path).suffix.lower() in IMAGE_EXTENSIONS


def encode_image(path: str) -> str:
    """将图片文件编码为 base64 data URI。

    Args:
        path: 图片文件路径

    Returns:
        data URI 字符串，如 data:image/png;base64,...

    Raises:
        FileNotFoundError: 文件不存在时
        ValueError: 不支持的图片格式
    """
    p = Path(path)
    ext = p.suffix.lower()
    if ext not in IMAGE_EXTENSIONS:
        raise ValueError(
            f"不支持的图片格式: {ext}，支持: {', '.join(IMAGE_EXTENSIONS)}"
        )
    mime = MIME_MAP.get(ext, "image/png")
    with open(p, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def build_vision_messages(data_uri: str) -> list[dict]:
    """构建带图片的 vision 消息 content array。

    Args:
        data_uri: base64 data URI

    Returns:
        OpenAI 格式的 messages 列表
    """
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": VISION_PROMPT},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ],
        }
    ]


class ImageProcessor:
    """多模态图片处理器。

    将图片编码后通过 vision-capable LLM 分析内容，生成笔记。
    """

    def __init__(self, provider):
        """初始化。

        Args:
            provider: LLMProvider 实例，需支持 vision content array
        """
        self._provider = provider

    def analyze(
        self,
        image_path: str,
        model: str | None = None,
    ) -> dict:
        """分析图片内容。

        Args:
            image_path: 图片文件路径
            model: LLM 模型名称，为 None 时使用 provider 默认模型

        Returns:
            {"analysis": str（LLM 返回的分析文本）, "image_path": str}

        Raises:
            FileNotFoundError: 图片文件不存在
            ValueError: 不支持的图片格式
        """
        data_uri = encode_image(image_path)
        messages = build_vision_messages(data_uri)

        kwargs = {"messages": messages}
        if model is not None:
            kwargs["model"] = model

        analysis = self._provider.chat(**kwargs)
        return {"analysis": analysis, "image_path": image_path}
