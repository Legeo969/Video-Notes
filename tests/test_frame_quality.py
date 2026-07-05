"""帧质量评分模块测试 — blur / brightness / contrast."""

import unittest
import numpy as np
from PIL import Image

from src.infrastructure.video.frame_quality import (
    laplacian_variance,
    is_blurry,
    mean_brightness,
    check_brightness,
    std_deviation,
    is_low_contrast,
)


class TestFrameQuality(unittest.TestCase):
    """帧质量评分单元测试 — 用 PIL 生成测试图片，无需外部文件。"""

    # ── 模糊检测 ──────────────────────────────────────────────

    def test_is_blurry(self):
        """全黑图片方差≈0，判定为模糊。"""
        img_black = Image.new("L", (100, 100), 0)
        self.assertTrue(is_blurry(img_black))

    def test_is_not_blurry(self):
        """随机噪点图片方差高，判定为清晰。"""
        arr = np.random.randint(0, 256, (100, 100), dtype=np.uint8)
        img_noise = Image.fromarray(arr, "L")
        self.assertFalse(is_blurry(img_noise))

    def test_laplacian_variance_black(self):
        """全黑图片的拉普拉斯方差应为 0。"""
        img_black = Image.new("L", (100, 100), 0)
        self.assertAlmostEqual(laplacian_variance(img_black), 0.0)

    # ── 亮度检测 ──────────────────────────────────────────────

    def test_mean_brightness_black(self):
        """全黑图片平均亮度为 0。"""
        img_black = Image.new("L", (100, 100), 0)
        self.assertEqual(mean_brightness(img_black), 0.0)

    def test_mean_brightness_white(self):
        """全白图片平均亮度为 255。"""
        img_white = Image.new("L", (100, 100), 255)
        self.assertEqual(mean_brightness(img_white), 255.0)

    def test_check_brightness_normal(self):
        """中等亮度图片判定为 normal。"""
        img = Image.new("L", (100, 100), 128)
        self.assertEqual(check_brightness(img), "normal")

    def test_check_brightness_too_dark(self):
        """全黑图片判定为 too_dark。"""
        img_black = Image.new("L", (100, 100), 0)
        self.assertEqual(check_brightness(img_black), "too_dark")

    def test_check_brightness_too_bright(self):
        """全白图片判定为 too_bright。"""
        img_white = Image.new("L", (100, 100), 255)
        self.assertEqual(check_brightness(img_white), "too_bright")

    # ── 对比度检测 ────────────────────────────────────────────

    def test_std_deviation_flat(self):
        """全灰图片标准差为 0。"""
        img_gray = Image.new("L", (100, 100), 128)
        self.assertAlmostEqual(std_deviation(img_gray), 0.0)

    def test_is_low_contrast(self):
        """全灰图片（std dev=0）判定为低对比度。"""
        img_gray = Image.new("L", (100, 100), 128)
        self.assertTrue(is_low_contrast(img_gray))

    def test_is_not_low_contrast(self):
        """随机噪点图片对比度高，判定为正常对比度。"""
        arr = np.random.randint(0, 256, (100, 100), dtype=np.uint8)
        img_noise = Image.fromarray(arr, "L")
        self.assertFalse(is_low_contrast(img_noise))


if __name__ == "__main__":
    unittest.main()
