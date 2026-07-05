"""Tests for image processor (multi-modal vision input)."""

import base64
import os
import struct
import tempfile
import zlib
from unittest import TestCase, mock

from src.application.vision.image_processor import (
    ImageProcessor,
    is_image,
    encode_image,
    build_vision_messages,
    VISION_PROMPT,
    IMAGE_EXTENSIONS,
    MIME_MAP,
)


def _make_test_png() -> bytes:
    """创建一个 1x1 像素的 RGB PNG。"""
    def _chunk(chunk_type, data):
        c = chunk_type + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
    sig = b'\x89PNG\r\n\x1a\n'
    ihdr = _chunk(b'IHDR', struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    raw = zlib.compress(b'\x00\xff\x00\x00')  # filter=None + RGB(255,0,0)
    idat = _chunk(b'IDAT', raw)
    iend = _chunk(b'IEND', b'')
    return sig + ihdr + idat + iend


def _make_test_jpg() -> bytes:
    """创建一个最小有效 JPEG（SOI + APP0/JFIF + EOI）。"""
    return (
        b'\xff\xd8'  # SOI
        b'\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00'  # APP0
        b'\xff\xd9'  # EOI
    )


class TestIsImage(TestCase):
    """测试 is_image 扩展名检测。"""

    def test_is_image_positive(self):
        """常见图片扩展名应返回 True。"""
        cases = [".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"]
        for ext in cases:
            with self.subTest(ext=ext):
                self.assertTrue(is_image(f"photo{ext}"))

    def test_is_image_positive_capitalized(self):
        """大写扩展名也应识别。"""
        self.assertTrue(is_image("photo.PNG"))
        self.assertTrue(is_image("photo.JPG"))
        self.assertTrue(is_image("photo.WebP"))

    def test_is_image_negative(self):
        """非图片扩展名应返回 False。"""
        cases = ["notes.txt", "document.pdf", "video.mp4", "script.py", "archive.zip"]
        for path in cases:
            with self.subTest(path=path):
                self.assertFalse(is_image(path))


class TestEncodeImage(TestCase):
    """测试图片文件 -> base64 data URI 编码。"""

    def _write_temp_file(self, suffix: str, content: bytes) -> str:
        fd, path = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(fd, 'wb') as f:
            f.write(content)
        self.addCleanup(os.remove, path)
        return path

    def test_encode_image_png(self):
        """PNG 文件应编码为 data:image/png;base64,..."""
        png_bytes = _make_test_png()
        path = self._write_temp_file(".png", png_bytes)
        result = encode_image(path)
        expected_prefix = "data:image/png;base64,"
        self.assertTrue(result.startswith(expected_prefix))
        encoded = result[len(expected_prefix):]
        self.assertEqual(base64.b64decode(encoded), png_bytes)

    def test_encode_image_jpg(self):
        """JPG 文件应编码为 data:image/jpeg;base64,..."""
        jpg_bytes = _make_test_jpg()
        path = self._write_temp_file(".jpg", jpg_bytes)
        result = encode_image(path)
        expected_prefix = "data:image/jpeg;base64,"
        self.assertTrue(result.startswith(expected_prefix))
        encoded = result[len(expected_prefix):]
        self.assertEqual(base64.b64decode(encoded), jpg_bytes)

    def test_encode_image_jpeg_extension(self):
        """.jpeg 扩展名也应正确映射到 image/jpeg。"""
        jpg_bytes = _make_test_jpg()
        path = self._write_temp_file(".jpeg", jpg_bytes)
        result = encode_image(path)
        expected_prefix = "data:image/jpeg;base64,"
        self.assertTrue(result.startswith(expected_prefix))
        encoded = result[len(expected_prefix):]
        self.assertEqual(base64.b64decode(encoded), jpg_bytes)

    def test_encode_image_webp(self):
        """WebP 文件编码。"""
        webp_bytes = b'RIFF\x00\x00\x00\x00WEBPVP8L'
        path = self._write_temp_file(".webp", webp_bytes)
        result = encode_image(path)
        expected_prefix = "data:image/webp;base64,"
        self.assertTrue(result.startswith(expected_prefix))
        encoded = result[len(expected_prefix):]
        self.assertEqual(base64.b64decode(encoded), webp_bytes)

    def test_encode_image_gif(self):
        """GIF 文件编码。"""
        gif_bytes = b'GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x00\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;'
        path = self._write_temp_file(".gif", gif_bytes)
        result = encode_image(path)
        expected_prefix = "data:image/gif;base64,"
        self.assertTrue(result.startswith(expected_prefix))
        encoded = result[len(expected_prefix):]
        self.assertEqual(base64.b64decode(encoded), gif_bytes)

    def test_encode_image_bmp(self):
        """BMP 文件编码。"""
        bmp_bytes = b'BM\x36\x00\x00\x00\x00\x00\x00\x00\x36\x00\x00\x00\x28\x00\x00\x00\x01\x00\x00\x00\x01\x00\x00\x00\x01\x00\x18\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
        path = self._write_temp_file(".bmp", bmp_bytes)
        result = encode_image(path)
        expected_prefix = "data:image/bmp;base64,"
        self.assertTrue(result.startswith(expected_prefix))
        encoded = result[len(expected_prefix):]
        self.assertEqual(base64.b64decode(encoded), bmp_bytes)

    def test_encode_image_unsupported_raises(self):
        """不支持的扩展名应抛出 ValueError。"""
        path = self._write_temp_file(".pdf", b"%PDF-1.4")
        with self.assertRaises(ValueError) as cm:
            encode_image(path)
        self.assertIn("不支持", str(cm.exception))
        self.assertIn(".pdf", str(cm.exception))

    def test_encode_image_no_extension(self):
        """无扩展名的文件应抛出 ValueError。"""
        path = self._write_temp_file("", b"no extension")
        with self.assertRaises(ValueError):
            encode_image(path)


class TestBuildVisionMessages(TestCase):
    """测试 vision prompt 消息构建。"""

    def test_build_messages_structure(self):
        """content array 应包含 text 和 image_url 两项。"""
        data_uri = "data:image/png;base64,AAAA"
        messages = build_vision_messages(data_uri)

        self.assertEqual(len(messages), 1)
        msg = messages[0]
        self.assertEqual(msg["role"], "user")
        self.assertIsInstance(msg["content"], list)
        self.assertEqual(len(msg["content"]), 2)

        # content[0]: text
        self.assertEqual(msg["content"][0]["type"], "text")
        self.assertEqual(msg["content"][0]["text"], VISION_PROMPT)

        # content[1]: image_url
        self.assertEqual(msg["content"][1]["type"], "image_url")
        self.assertEqual(msg["content"][1]["image_url"]["url"], data_uri)

    def test_build_messages_different_uri(self):
        """不同的 data URI 应正确反映在消息结构中。"""
        data_uri = "data:image/jpeg;base64,BBBB"
        messages = build_vision_messages(data_uri)
        self.assertEqual(messages[0]["content"][1]["image_url"]["url"], data_uri)


class TestImageProcessor(TestCase):
    """测试 ImageProcessor 集成。"""

    def test_analyze_returns_analysis(self):
        """analyze() 应调用 provider.chat() 并返回分析结果字典。"""
        provider = mock.Mock()
        mock_analysis = "这是一张风景图，画面中有山有水，色调偏暖..."
        provider.chat.return_value = mock_analysis

        fd, path = tempfile.mkstemp(suffix=".png")
        with os.fdopen(fd, 'wb') as f:
            f.write(_make_test_png())

        try:
            processor = ImageProcessor(provider)
            result = processor.analyze(path)

            self.assertIn("analysis", result)
            self.assertIn("image_path", result)
            self.assertEqual(result["analysis"], mock_analysis)
            self.assertEqual(result["image_path"], path)

            # 验证 provider.chat 被调用且参数正确
            provider.chat.assert_called_once()
            _, kwargs = provider.chat.call_args
            messages = kwargs["messages"]
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0]["role"], "user")
            content = messages[0]["content"]
            self.assertEqual(content[0]["type"], "text")
            self.assertEqual(content[1]["type"], "image_url")
            self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))
        finally:
            os.unlink(path)

    def test_analyze_passes_model_to_provider(self):
        """analyze() 应透传 model 参数给 provider.chat()。"""
        provider = mock.Mock()
        provider.chat.return_value = "analysis result"

        fd, path = tempfile.mkstemp(suffix=".jpg")
        with os.fdopen(fd, 'wb') as f:
            f.write(_make_test_jpg())

        try:
            processor = ImageProcessor(provider)
            processor.analyze(path, model="gpt-4o")
            provider.chat.assert_called_once_with(
                messages=mock.ANY,
                model="gpt-4o",
            )
        finally:
            os.unlink(path)

    def test_analyze_image_not_found(self):
        """不存在的图片路径应抛出 FileNotFoundError。"""
        provider = mock.Mock()
        processor = ImageProcessor(provider)
        with self.assertRaises(FileNotFoundError):
            processor.analyze("/nonexistent/image.png")

    def test_analyze_unsupported_format_raises(self):
        """不支持的格式应抛出 ValueError。"""
        provider = mock.Mock()
        processor = ImageProcessor(provider)
        with self.assertRaises(ValueError):
            processor.analyze("document.pdf")
