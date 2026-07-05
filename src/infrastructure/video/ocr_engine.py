"""OCR 引擎封装 — 使用 PaddleOCR 惰性加载。"""

import logging
import threading

logger = logging.getLogger(__name__)


class OCREngine:
    """OCR 引擎，惰性加载 PaddleOCR 模型。

    PaddleOCR 是重量级库，初始化耗时且占用显存/内存，
    因此采用 lazy initialization：调用方构造 OCREngine 时
    不加载模型，首次调用 ocr_frame() 时才实例化 PaddleOCR。

    初始化失败（如缺少依赖）最多尝试一次 GPU 和一次 CPU，
    两次均失败后本任务彻底关闭 OCR。

    Args:
        lang: 识别语言，默认 "ch"（中文）
        use_gpu: 保留的兼容参数；PaddleOCR 3.x 会自行选择可用设备
        **kwargs: 透传给 PaddleOCR 的额外参数
    """

    def __init__(self, lang: str = "ch", use_gpu: bool = True, *, raise_on_error: bool = False, **kwargs):
        self._lang = lang
        self._use_gpu = use_gpu
        self._device = kwargs.pop("device", None)
        self._raise_on_error = bool(raise_on_error)
        self._last_error: str | None = None
        defaults = {
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": False,
        }
        defaults.update(kwargs)
        self._kwargs = defaults
        self._ocr = None
        self._lock = threading.Lock()
        self._init_attempted = False
        self._disabled_reason: str | None = None

    def is_available(self) -> bool:
        """OCR 是否可用（初始化成功或尚未尝试）。"""
        return self._disabled_reason is None

    def disabled_reason(self) -> str | None:
        """返回禁用原因，无则返回 None。"""
        return self._disabled_reason

    def last_error(self) -> str | None:
        """返回最近一次推理错误，便于隔离进程区分“无文字”和“推理失败”。"""
        return self._last_error

    def _get_ocr(self):
        """惰性获取 PaddleOCR 实例，失败后不再重试。"""
        if self._ocr is not None:
            return self._ocr

        if self._init_attempted:
            return None

        with self._lock:
            if self._ocr is not None:
                return self._ocr
            if self._init_attempted:
                return None
            self._init_attempted = True

            devices = self._resolve_devices()
            errors: list[str] = []

            for device in devices:
                try:
                    from paddleocr import PaddleOCR

                    logger.info("Initializing PaddleOCR on %s", device)
                    self._ocr = PaddleOCR(
                        lang=self._lang,
                        device=device,
                        **self._kwargs,
                    )
                    self._device = device
                    return self._ocr
                except Exception as e:
                    errors.append(f"{device}: {e}")
                    logger.exception("PaddleOCR initialization failed on %s", device)

            self._disabled_reason = "; ".join(errors)
            logger.error("PaddleOCR initialization failed on all devices: %s", self._disabled_reason)
            return None

    def _resolve_devices(self) -> list[str]:
        """返回要尝试的设备列表（GPU 优先，CPU 回退）。"""
        if self._device:
            return [self._device]
        if not self._use_gpu:
            return ["cpu"]

        try:
            import paddle

            has_cuda = paddle.device.is_compiled_with_cuda()
            device_count = paddle.device.cuda.device_count() if has_cuda else 0
            if has_cuda and device_count > 0:
                return ["gpu:0", "cpu"]
        except Exception as e:
            logger.warning("PaddleOCR GPU check failed; falling back to CPU: %s", e)

        return ["cpu"]

    def ocr_frame(self, image_path: str) -> list[dict]:
        """识别帧图片中的文字。

        Args:
            image_path: 图片路径

        Returns:
            文字结果列表，每项包含:
                - text (str): 识别的文字
                - confidence (float): 置信度 (0-1)
                - bbox (list[list[float]]): 四边形四点坐标 [[x,y],...]
            无文字或失败时返回空列表。
        """
        ocr = self._get_ocr()
        if ocr is None:
            return []
        try:
            if hasattr(ocr, "predict"):
                result = ocr.predict(image_path)
                if result is not None and not isinstance(result, list):
                    result = list(result)
            else:
                result = ocr.ocr(image_path, cls=True)
            self._last_error = None
            return self._format_results(result)
        except Exception as e:
            self._last_error = str(e)
            logger.warning("OCR failed for %s: %s", image_path, e)
            if self._raise_on_error:
                raise
            return []

    @staticmethod
    def _format_results(raw: list | None) -> list[dict]:
        """将 PaddleOCR 原始输出转换为统一格式。

        原始格式::

            [
                [  # image index 0
                    (bbox, (text, confidence)),
                    ...
                ]
            ]

        转换为::

            [
                {"text": str, "confidence": float, "bbox": list[list[float]]},
                ...
            ]
        """
        if not raw:
            return []

        first = raw[0]
        if hasattr(first, "get") and first.get("rec_texts") is not None:
            texts = first.get("rec_texts") or []
            scores = first.get("rec_scores") or []
            polys = first.get("rec_polys") or first.get("dt_polys") or []
            formatted = []
            for idx, text in enumerate(texts):
                bbox = polys[idx] if idx < len(polys) else []
                if hasattr(bbox, "tolist"):
                    bbox = bbox.tolist()
                formatted.append(
                    {
                        "text": text,
                        "confidence": float(scores[idx]) if idx < len(scores) else 0.0,
                        "bbox": bbox,
                    }
                )
            return formatted

        if not first:
            return []
        formatted = []
        for entry in first:
            try:
                bbox, (text, confidence) = entry
                formatted.append(
                    {
                        "text": text,
                        "confidence": confidence,
                        "bbox": bbox,
                    }
                )
            except (ValueError, TypeError) as e:
                logger.debug("Skipping malformed OCR entry %s: %s", entry, e)
                continue
        return formatted