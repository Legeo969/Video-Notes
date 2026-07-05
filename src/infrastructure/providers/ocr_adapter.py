"""OCR provider — 将 OcrEngine 适配到 Provider ABC。"""

from src.domain.interfaces.provider import Provider, ProviderConfig


class OCRProvider(Provider):
    """OCR provider，包装 OcrEngine 提供文字识别能力。

    不依赖 API key / base_url，使用本地 PaddleOCR 引擎。
    """

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._engine = None  # 惰性加载

    def _get_engine(self):
        if self._engine is None:
            from src.infrastructure.video.ocr_engine import OCREngine

            self._engine = OCREngine()
        return self._engine

    def ocr(self, image_path: str) -> str:
        """识别图片中的文字，返回拼接的文本内容。

        Args:
            image_path: 图片文件路径

        Returns:
            识别的全部文本，按检测顺序用换行分隔。无文字时返回空字符串。
        """
        engine = self._get_engine()
        results = engine.ocr_frame(image_path)
        if not results:
            return ""
        return "\n".join(item["text"] for item in results if item.get("text"))

    def chat(self, messages: list[dict], **kwargs) -> str:
        raise NotImplementedError("OCRProvider does not support chat")

    def embed(self, texts: list[str], **kwargs) -> list[list[float]]:
        raise NotImplementedError("OCRProvider does not support embed")

    def vision(self, image_path: str, prompt: str, **kwargs) -> str:
        raise NotImplementedError("OCRProvider does not support vision")
