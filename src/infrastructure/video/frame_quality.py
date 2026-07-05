"""帧质量评分模块 — 模糊检测、亮度检测、对比度检测。"""

from PIL import Image
import math


def _to_grayscale(img: Image.Image) -> Image.Image:
    """转换为灰度图（已为灰度则跳过）。"""
    return img.convert("L") if img.mode != "L" else img


def laplacian_variance(img: Image.Image) -> float:
    """计算拉普拉斯方差，衡量图像清晰度。值越低越模糊。"""
    gray = _to_grayscale(img)
    pixels = list(gray.get_flattened_data())
    w, h = gray.size
    variance = 0.0
    count = 0
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            idx = y * w + x
            lap = (
                -pixels[idx]
                + 0.25 * (
                    pixels[idx - w] + pixels[idx + w]
                    + pixels[idx - 1] + pixels[idx + 1]
                )
            )
            variance += lap * lap
            count += 1
    return variance / count if count else 0.0


def is_blurry(img: Image.Image, threshold: float = 100.0) -> bool:
    """判断图片是否模糊。"""
    return laplacian_variance(img) < threshold


def mean_brightness(img: Image.Image) -> float:
    """计算图片平均亮度（0-255）。"""
    gray = _to_grayscale(img)
    pixels = list(gray.get_flattened_data())
    total = sum(pixels)
    count = len(pixels)
    return total / count if count else 0.0


def check_brightness(
    img: Image.Image,
    dark_threshold: float = 25.0,
    bright_threshold: float = 230.0,
) -> str:
    """检测亮度状态。返回 'normal' / 'too_dark' / 'too_bright'。"""
    brightness = mean_brightness(img)
    if brightness < dark_threshold:
        return "too_dark"
    if brightness > bright_threshold:
        return "too_bright"
    return "normal"


def std_deviation(img: Image.Image) -> float:
    """计算像素标准差，衡量对比度。"""
    gray = _to_grayscale(img)
    pixels = list(gray.get_flattened_data())
    n = len(pixels)
    mean = sum(pixels) / n
    variance = sum((p - mean) ** 2 for p in pixels) / n
    return math.sqrt(variance)


def is_low_contrast(img: Image.Image, threshold: float = 40.0) -> bool:
    """判断图片是否对比度过低。"""
    return std_deviation(img) < threshold