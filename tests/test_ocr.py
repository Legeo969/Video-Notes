"""OCR 引擎封装测试 — 使用 mock 避免 PaddleOCR 依赖。"""

import sys
import unittest
from unittest.mock import patch, MagicMock

# 如果 paddleocr 未安装，注入 fake module 确保 @patch 能解析
# 如果已安装则使用真实模块路径
if "paddleocr" not in sys.modules:
    try:
        import paddleocr  # noqa: F401
    except ImportError:
        sys.modules["paddleocr"] = MagicMock()

from src.infrastructure.video.ocr_engine import OCREngine


class TestOCREngineLazyInit(unittest.TestCase):
    """惰性加载行为测试。"""

    def test_init_does_not_load_paddleocr(self):
        """初始化时不加载 PaddleOCR，首次调用 ocr_frame 时才实例化。"""
        engine = OCREngine()
        self.assertIsNone(engine._ocr)

    @patch("paddleocr.PaddleOCR")
    def test_lazy_load_on_first_call(self, mock_paddleocr):
        """首次调用时自动实例化 PaddleOCR。"""
        engine = OCREngine(lang="ch", use_gpu=False)
        mock_instance = MagicMock()
        mock_instance.predict.return_value = None
        mock_paddleocr.return_value = mock_instance

        engine.ocr_frame("dummy.jpg")

        mock_paddleocr.assert_called_once_with(
            lang="ch",
            device="cpu",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
        self.assertIsNotNone(engine._ocr)

    @patch("paddleocr.PaddleOCR")
    def test_lazy_load_only_once(self, mock_paddleocr):
        """多次调用只实例化一次 PaddleOCR。"""
        mock_instance = MagicMock()
        mock_instance.predict.return_value = None
        mock_paddleocr.return_value = mock_instance

        engine = OCREngine(lang="en", use_gpu=False)
        engine.ocr_frame("img1.jpg")
        engine.ocr_frame("img2.jpg")

        mock_paddleocr.assert_called_once()
        self.assertEqual(mock_instance.predict.call_count, 2)

    @patch("paddleocr.PaddleOCR")
    def test_extra_kwargs_passed_to_paddleocr(self, mock_paddleocr):
        """额外 kwargs 透传给 PaddleOCR 构造函数。"""
        mock_instance = MagicMock()
        mock_instance.predict.return_value = None
        mock_paddleocr.return_value = mock_instance

        engine = OCREngine(lang="ch", use_gpu=False, det_db_thresh=0.3)
        engine.ocr_frame("img.jpg")

        mock_paddleocr.assert_called_once_with(
            lang="ch",
            device="cpu",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            det_db_thresh=0.3,
        )

    @patch("paddleocr.PaddleOCR")
    def test_gpu_device_is_selected_when_paddle_cuda_available(self, mock_paddleocr):
        mock_instance = MagicMock()
        mock_instance.predict.return_value = None
        mock_paddleocr.return_value = mock_instance

        fake_paddle = MagicMock()
        fake_paddle.device.is_compiled_with_cuda.return_value = True
        fake_paddle.device.cuda.device_count.return_value = 1

        with patch.dict(sys.modules, {"paddle": fake_paddle}):
            engine = OCREngine(lang="ch", use_gpu=True)
            engine.ocr_frame("img.jpg")

        mock_paddleocr.assert_called_once_with(
            lang="ch",
            device="gpu:0",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )

    @patch("paddleocr.PaddleOCR")
    def test_cpu_device_is_used_when_paddle_cuda_unavailable(self, mock_paddleocr):
        mock_instance = MagicMock()
        mock_instance.predict.return_value = None
        mock_paddleocr.return_value = mock_instance

        fake_paddle = MagicMock()
        fake_paddle.device.is_compiled_with_cuda.return_value = False

        with patch.dict(sys.modules, {"paddle": fake_paddle}):
            engine = OCREngine(lang="ch", use_gpu=True)
            engine.ocr_frame("img.jpg")

        mock_paddleocr.assert_called_once_with(
            lang="ch",
            device="cpu",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )


class TestOCRFrameFormattedResults(unittest.TestCase):
    """OCR 结果格式转换测试。"""

    def _mock_raw_result(self):
        """构造 PaddleOCR 的原始返回格式（单行文字）。"""
        return [
            [
                ([[10.0, 10.0], [100.0, 10.0], [100.0, 40.0], [10.0, 40.0]],
                 ("Hello World", 0.95)),
            ]
        ]

    def _mock_multi_line_result(self):
        """多行文字返回。"""
        return [
            [
                ([[0, 0], [50, 0], [50, 20], [0, 20]], ("Line1", 0.98)),
                ([[0, 30], [60, 30], [60, 50], [0, 50]], ("Line2", 0.85)),
            ]
        ]

    def _mock_modern_result(self):
        """PaddleOCR 3.x predict 返回格式。"""
        return [
            {
                "rec_texts": ["API KEY BASE URL OCR TEST 123"],
                "rec_scores": [0.9887],
                "rec_polys": [[[77, 306], [1263, 306], [1263, 371], [77, 371]]],
            }
        ]

    @patch("paddleocr.PaddleOCR")
    def test_single_line_formatted(self, mock_paddleocr):
        """单行文字正确转换。"""
        mock_instance = MagicMock()
        mock_instance.predict.return_value = self._mock_raw_result()
        mock_paddleocr.return_value = mock_instance

        engine = OCREngine(lang="en", use_gpu=False)
        results = engine.ocr_frame("test.jpg")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["text"], "Hello World")
        self.assertAlmostEqual(results[0]["confidence"], 0.95)
        self.assertEqual(len(results[0]["bbox"]), 4)

    @patch("paddleocr.PaddleOCR")
    def test_multi_line_formatted(self, mock_paddleocr):
        """多行文字全部转换。"""
        mock_instance = MagicMock()
        mock_instance.predict.return_value = self._mock_multi_line_result()
        mock_paddleocr.return_value = mock_instance

        engine = OCREngine(lang="en", use_gpu=False)
        results = engine.ocr_frame("test.jpg")

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["text"], "Line1")
        self.assertEqual(results[1]["text"], "Line2")
        self.assertAlmostEqual(results[0]["confidence"], 0.98)
        self.assertAlmostEqual(results[1]["confidence"], 0.85)

    @patch("paddleocr.PaddleOCR")
    def test_modern_predict_result_formatted(self, mock_paddleocr):
        """PaddleOCR 3.x predict 结果正确转换。"""
        mock_instance = MagicMock()
        mock_instance.predict.return_value = self._mock_modern_result()
        mock_paddleocr.return_value = mock_instance

        engine = OCREngine(lang="en", use_gpu=False)
        results = engine.ocr_frame("test.jpg")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["text"], "API KEY BASE URL OCR TEST 123")
        self.assertAlmostEqual(results[0]["confidence"], 0.9887)
        self.assertEqual(len(results[0]["bbox"]), 4)

    @patch("paddleocr.PaddleOCR")
    def test_confidence_and_bbox_types(self, mock_paddleocr):
        """confidence 为 float，bbox 为嵌套 list。"""
        mock_instance = MagicMock()
        mock_instance.predict.return_value = self._mock_raw_result()
        mock_paddleocr.return_value = mock_instance

        engine = OCREngine(lang="en", use_gpu=False)
        results = engine.ocr_frame("test.jpg")

        self.assertIsInstance(results[0]["confidence"], float)
        self.assertIsInstance(results[0]["bbox"], list)
        for point in results[0]["bbox"]:
            self.assertIsInstance(point, list)
            self.assertEqual(len(point), 2)

    @patch("paddleocr.PaddleOCR")
    def test_predict_called_for_modern_paddleocr(self, mock_paddleocr):
        """ocr_frame 调用 PaddleOCR 3.x 的 predict API。"""
        mock_instance = MagicMock()
        mock_instance.predict.return_value = None
        mock_paddleocr.return_value = mock_instance

        engine = OCREngine(lang="en", use_gpu=False)
        engine.ocr_frame("test.jpg")

        mock_instance.predict.assert_called_once_with("test.jpg")


class TestOCRFrameNoText(unittest.TestCase):
    """无文字场景测试。"""

    @patch("paddleocr.PaddleOCR")
    def test_none_result_returns_empty(self, mock_paddleocr):
        """PaddleOCR 返回 None 时返回空列表。"""
        mock_instance = MagicMock()
        mock_instance.predict.return_value = None
        mock_paddleocr.return_value = mock_instance

        engine = OCREngine(lang="en", use_gpu=False)
        results = engine.ocr_frame("test.jpg")
        self.assertEqual(results, [])

    @patch("paddleocr.PaddleOCR")
    def test_empty_first_element_returns_empty(self, mock_paddleocr):
        """raw[0] 为空时返回空列表。"""
        mock_instance = MagicMock()
        mock_instance.predict.return_value = [[]]
        mock_paddleocr.return_value = mock_instance

        engine = OCREngine(lang="en", use_gpu=False)
        results = engine.ocr_frame("test.jpg")
        self.assertEqual(results, [])

    @patch("paddleocr.PaddleOCR")
    def test_empty_result_list_returns_empty(self, mock_paddleocr):
        """raw 为空列表时返回空列表。"""
        mock_instance = MagicMock()
        mock_instance.predict.return_value = []
        mock_paddleocr.return_value = mock_instance

        engine = OCREngine(lang="en", use_gpu=False)
        results = engine.ocr_frame("test.jpg")
        self.assertEqual(results, [])

    @patch("paddleocr.PaddleOCR")
    def test_predict_with_no_text_image(self, mock_paddleocr):
        """无文字图片返回空字符串结果时仍正确转换。"""
        mock_instance = MagicMock()
        mock_instance.predict.return_value = [[
            ([[0, 0], [0, 0], [0, 0], [0, 0]], ("", 0.0))
        ]]
        mock_paddleocr.return_value = mock_instance

        engine = OCREngine(lang="en", use_gpu=False)
        results = engine.ocr_frame("blank.jpg")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["text"], "")


class TestOCRFrameErrorHandling(unittest.TestCase):
    """OCR 异常处理测试。"""

    @patch("paddleocr.PaddleOCR")
    def test_ocr_failure_returns_empty(self, mock_paddleocr):
        """OCR 调用抛异常时返回空列表不抛异常。"""
        mock_instance = MagicMock()
        mock_instance.predict.side_effect = RuntimeError("OCR failed")
        mock_paddleocr.return_value = mock_instance

        engine = OCREngine(lang="en", use_gpu=False)
        results = engine.ocr_frame("test.jpg")
        self.assertEqual(results, [])

    @patch("paddleocr.PaddleOCR")
    def test_init_failure_raises_on_first_call(self, mock_paddleocr):
        """PaddleOCR 初始化失败后引擎降级为不可用，不抛异常。"""
        mock_paddleocr.side_effect = ImportError("No module")

        engine = OCREngine(lang="en", use_gpu=False)
        result = engine.ocr_frame("test.jpg")

        self.assertEqual(result, [])
        self.assertFalse(engine.is_available())
        self.assertIsNotNone(engine.disabled_reason())

    @patch("paddleocr.PaddleOCR")
    def test_format_results_with_malformed_data(self, mock_paddleocr):
        """异常数据格式仍返回空列表。"""
        mock_instance = MagicMock()
        mock_instance.predict.return_value = [[
            "not_a_tuple"
        ]]
        mock_paddleocr.return_value = mock_instance

        engine = OCREngine(lang="en", use_gpu=False)
        results = engine.ocr_frame("test.jpg")
        self.assertEqual(results, [])

    @patch("paddleocr.PaddleOCR")
    def test_consecutive_failures_returns_empty(self, mock_paddleocr):
        """连续失败每次均返回空列表。"""
        mock_instance = MagicMock()
        mock_instance.predict.side_effect = Exception("fail")
        mock_paddleocr.return_value = mock_instance

        engine = OCREngine(lang="en", use_gpu=False)
        r1 = engine.ocr_frame("img1.jpg")
        r2 = engine.ocr_frame("img2.jpg")
        self.assertEqual(r1, [])
        self.assertEqual(r2, [])


# 如果是我们注入的 fake module，测试完成后清理
def tearDownModule():
    m = sys.modules.get("paddleocr")
    if m is not None and isinstance(m, MagicMock):
        sys.modules.pop("paddleocr", None)


if __name__ == "__main__":
    unittest.main()
