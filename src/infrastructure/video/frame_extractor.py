"""视频帧提取模块 - 使用 FFmpeg 进行智能帧提取（场景检测 + 质量评分 + 去重）。"""

import subprocess
from src.utils.subprocess_flags import hidden_subprocess_kwargs
import os
import re
import math
import logging
from pathlib import Path
from PIL import Image

from src.infrastructure.video.frame_quality import is_blurry, check_brightness, is_low_contrast
from src.utils.external_tools import resolve_tool

logger = logging.getLogger(__name__)


# ── 工具函数 ──────────────────────────────────────────────────


def _sanitize_filename(name: str) -> str:
    """将文件名中的非 ASCII 字符替换为 '_'，确保帧文件名 ASCII-safe。"""
    safe = re.sub(r'[^\x00-\x7F]', '_', name)
    return safe or 'video'


def _bhattacharyya_distance(h1: list[int], h2: list[int]) -> float:
    """巴氏距离，衡量两个 256-bin 直方图的相似度。

    0 = 完全相同，1 = 完全不同。
    """
    total = 0.0
    for a, b in zip(h1, h2):
        total += math.sqrt(a * b)
    sum_product = sum(h1) * sum(h2)
    if sum_product == 0:
        return 1.0
    return math.sqrt(1.0 - total / math.sqrt(sum_product))


def _parse_scene_times(ffmpeg_output: str) -> list[float]:
    """从 FFmpeg showinfo 输出中解析场景切分时间点（秒）。

    解析形如 'pts_time:96' 的标记，返回所有 pts_time 值。
    """
    times = []
    for line in ffmpeg_output.splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            times.append(float(m.group(1)))
    return times


def _get_duration(video_path: str) -> float | None:
    """获取视频总时长（秒）。

    优先使用 ffprobe，失败时回退到 PyAV (av) 库。
    """
    # ── 方法 1: ffprobe ──
    ffprobe = resolve_tool("ffprobe", components=["ffmpeg-tools"], provides="ffmpeg")
    cmd = [
        ffprobe or "ffprobe",
        "-nostdin", "-hide_banner", "-loglevel", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        video_path,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, errors="replace", timeout=30,
            **hidden_subprocess_kwargs(),
        )
        dur = float(result.stdout.strip())
        if dur > 0:
            return dur
    except FileNotFoundError:
        pass  # ffprobe 不在 PATH，回退到 av
    except subprocess.TimeoutExpired:
        logger.warning("ffprobe 超时，尝试 av 库获取时长")
    except (ValueError, IndexError):
        pass  # ffprobe 输出无法解析，回退到 av

    # ── 方法 2: PyAV (av) 库回退 ──
    try:
        import av
        container = av.open(video_path)
        dur = float(container.duration) / 1_000_000  # av 返回微秒
        container.close()
        if dur > 0:
            return dur
    except Exception as exc:
        logger.warning("av 库获取时长也失败: %s", exc)

    return None


def _detect_scenes_ffmpeg(video_path: str, threshold: float = 0.3) -> list[float]:
    """使用 FFmpeg scene filter 检测场景切分时间点（秒）。

    通过 select='gt(scene,{threshold})' 过滤帧间变化超过阈值的帧，
    搭配 showinfo 输出 pts_time。结果包含每个检测到的切分帧的时间戳。
    """
    ffmpeg = resolve_tool("ffmpeg", components=["ffmpeg-tools"], provides="ffmpeg")
    cmd = [
        ffmpeg or "ffmpeg",
        "-nostdin", "-hide_banner", "-loglevel", "error",
        "-i", video_path,
        "-vf", f"select='gt(scene,{threshold})',showinfo",
        "-vsync", "vfr",
        "-f", "null",
        "-",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, errors="replace", timeout=120,
            **hidden_subprocess_kwargs(),
        )
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg 场景检测超时")
        return []
    # showinfo 输出在 stderr
    return _parse_scene_times(result.stderr)


def _detect_scenes_pyscenedetect(
    video_path: str, threshold: float = 0.3
) -> list[float] | None:
    try:
        from scenedetect import ContentDetector, detect
    except Exception:
        return None

    try:
        detector = ContentDetector(threshold=max(1.0, threshold * 100.0))
        scenes = detect(video_path, detector, show_progress=False)
    except Exception as exc:
        logger.warning("PySceneDetect failed, falling back to ffmpeg: %s", exc)
        return None

    times: list[float] = []
    for scene_start, _scene_end in scenes:
        try:
            second = float(scene_start.get_seconds())
        except Exception:
            continue
        if second > 0:
            times.append(second)
    return sorted(times)


def _detect_scenes(video_path: str, threshold: float = 0.3) -> list[float]:
    scenes = _detect_scenes_pyscenedetect(video_path, threshold)
    if scenes is not None:
        return scenes
    return _detect_scenes_ffmpeg(video_path, threshold)


def _generate_candidate_times(
    scene_times: list[float], total_duration: float
) -> list[float]:
    """根据场景段划分生成候选帧时间点。

    场景段的策略：
    - 场景 ≤ 5s → 1 张（中间点）
    - 5s < 场景 ≤ 30s → 2 张（1/3、2/3 处）
    - 场景 > 30s → 3 张（1/4、1/2、3/4 处）

    如果 scene_times 为空（无场景切分），返回空列表触发保底逻辑。
    """
    if not scene_times:
        return []

    segments: list[tuple[float, float]] = []
    prev = 0.0
    for t in scene_times:
        if t > prev:
            segments.append((prev, t))
        prev = t
    if prev < total_duration:
        segments.append((prev, total_duration))

    times: list[float] = []
    for start, end in segments:
        duration = end - start
        if duration <= 0:
            continue
        if duration <= 5:
            times.append((start + end) / 2.0)
        elif duration <= 30:
            times.append(start + duration / 3.0)
            times.append(start + 2.0 * duration / 3.0)
        else:
            times.append(start + duration / 4.0)
            times.append(start + duration / 2.0)
            times.append(start + 3.0 * duration / 4.0)

    return sorted(times)


def _limit_evenly(times: list[float], max_frames: int) -> list[float]:
    if max_frames <= 0 or len(times) <= max_frames:
        return sorted(times)
    if max_frames == 1:
        return [times[len(times) // 2]]

    ordered = sorted(times)
    step = (len(ordered) - 1) / (max_frames - 1)
    indexes = [round(i * step) for i in range(max_frames)]
    return [ordered[i] for i in indexes]


def _generate_auto_candidate_times(
    scene_times: list[float],
    total_duration: float,
    max_frames: int = 30,
) -> list[float]:
    """Generate scene-aware candidate times bounded by max_frames."""
    if total_duration <= 0 or max_frames <= 0:
        return []

    candidate_times = _generate_candidate_times(scene_times, total_duration)
    if not candidate_times:
        interval = max(1.0, total_duration / max_frames)
        candidate_times = []
        t = interval / 2.0
        while t < total_duration:
            candidate_times.append(t)
            t += interval

    return _limit_evenly(candidate_times, max_frames)


_TRANSCRIPT_KEYWORDS = (
    "第一步", "第二步", "第三步", "步骤", "参数", "设置", "提示词", "注意",
    "关键", "示例", "代码", "公式", "界面", "按钮", "菜单", "输入", "输出",
    "step", "setting", "prompt", "note", "warning", "example", "code",
)


def _extract_transcript_candidate_times(
    transcript_segments: list[dict] | None,
    total_duration: float,
) -> list[float]:
    if not transcript_segments:
        return []

    times: list[float] = []
    for segment in transcript_segments:
        text = str(segment.get("text", "")).lower()
        if not any(keyword.lower() in text for keyword in _TRANSCRIPT_KEYWORDS):
            continue
        try:
            start = float(segment.get("start", 0.0))
            end = float(segment.get("end", start))
        except (TypeError, ValueError):
            continue
        t = max(0.0, min(total_duration, (start + end) / 2.0))
        times.append(t)
    return times


def _merge_candidate_times(
    visual_times: list[float],
    transcript_segments: list[dict] | None,
    total_duration: float,
    max_frames: int = 30,
    min_gap_sec: float = 3.0,
) -> list[float]:
    """Merge visual scene times with transcript keyword times and de-nearby points."""
    transcript_times = _extract_transcript_candidate_times(
        transcript_segments,
        total_duration,
    )
    merged = sorted(
        t for t in [*visual_times, *transcript_times]
        if 0.0 <= t <= total_duration
    )

    result: list[float] = []
    for t in merged:
        if result and abs(t - result[-1]) < min_gap_sec:
            continue
        result.append(t)

    return _limit_evenly(result, max_frames)


# ── 帧操作 ────────────────────────────────────────────────────


def _extract_frame_at_time(
    video_path: str, output_dir: str, base_name: str, time_sec: float, index: int
) -> dict | None:
    """在指定时间点提帧并保存为 JPEG。

    Returns:
        帧信息字典，失败时返回 None。
    """
    filename = f"frame_{base_name}_{index:04d}.jpg"
    out_path = os.path.join(output_dir, filename)
    ffmpeg = resolve_tool("ffmpeg", components=["ffmpeg-tools"], provides="ffmpeg")
    cmd = [
        ffmpeg or "ffmpeg",
        "-nostdin", "-hide_banner", "-loglevel", "error",
        "-ss", str(time_sec),
        "-i", video_path,
        "-vframes", "1",
        "-q:v", "5",
        "-y",
        out_path,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, errors="replace", timeout=30,
            **hidden_subprocess_kwargs(),
        )
    except subprocess.TimeoutExpired:
        logger.warning("提帧超时 time=%.2f", time_sec)
        return None
    if result.returncode != 0 or not os.path.exists(out_path):
        logger.warning("提帧失败 time=%.2f: %s", time_sec, result.stderr[:200])
        return None
    return {
        "path": str(out_path),
        "filename": filename,
        "timestamp_sec": time_sec,
    }


def _load_histogram(image_path: str) -> list[int] | None:
    """加载图片的 256-bin 亮度直方图。加载失败时返回 None。"""
    try:
        img = Image.open(image_path).convert("L")
        return img.histogram()
    except Exception:
        return None


def _deduplicate_frames_by_imagehash(
    frames: list[dict], threshold: int = 4
) -> list[dict] | None:
    try:
        import imagehash
    except Exception:
        return None

    kept: list[dict] = []
    prev_hash = None
    prev_hist = None
    for frame in frames:
        try:
            with Image.open(frame["path"]) as img:
                curr_hash = imagehash.phash(img)
        except Exception:
            kept.append(frame)
            prev_hash = None
            prev_hist = None
            continue

        curr_hist = _load_histogram(frame["path"])
        hash_distance = (curr_hash - prev_hash) if prev_hash is not None else None
        hist_distance = 0.0
        if prev_hist is not None and curr_hist is not None:
            hist_distance = _bhattacharyya_distance(prev_hist, curr_hist)

        if (
            prev_hash is None
            or hash_distance > threshold
            or hist_distance >= 0.05
        ):
            kept.append(frame)
            prev_hash = curr_hash
            prev_hist = curr_hist

    return kept


def _deduplicate_frames(
    frames: list[dict], threshold: float = 0.05
) -> list[dict]:
    """直方图去重。

    从第一帧开始，保留与上一保留帧的巴氏距离 >= threshold 的帧。
    """
    if not frames:
        return frames

    imagehash_result = _deduplicate_frames_by_imagehash(frames)
    if imagehash_result is not None:
        return imagehash_result

    result: list[dict] = [frames[0]]
    prev_hist = _load_histogram(frames[0]["path"])
    if prev_hist is None:
        return frames  # 无法加载直方图，保守返回全部

    for frame in frames[1:]:
        curr_hist = _load_histogram(frame["path"])
        if curr_hist is None:
            result.append(frame)
            prev_hist = None
            continue
        if prev_hist is None:
            result.append(frame)
            prev_hist = curr_hist
            continue
        dist = _bhattacharyya_distance(prev_hist, curr_hist)
        if dist >= threshold:
            result.append(frame)
            prev_hist = curr_hist
        # 距离 < threshold: 跳过该帧（重复）

    return result


def _normalize_ocr_text(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _deduplicate_ocr_text_frames(frames: list[dict]) -> list[dict]:
    """Remove later frames whose OCR text is the same as a kept frame."""
    result: list[dict] = []
    seen_texts: set[str] = set()

    for frame in frames:
        text = _normalize_ocr_text(str(frame.get("ocr_text", "")))
        if text and text in seen_texts:
            continue
        result.append(frame)
        if text:
            seen_texts.add(text)

    return result


def _opencv_rejects_frame(
    image_path: str,
    blur_threshold: float = 100.0,
) -> bool | None:
    try:
        import cv2
    except Exception:
        return None

    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None

    blur_score = cv2.Laplacian(img, cv2.CV_64F).var()
    brightness = float(img.mean())
    contrast = float(img.std())
    return blur_score < blur_threshold or brightness < 25.0 or brightness > 230.0 or contrast < 40.0


def _pil_rejects_frame(image_path: str, blur_threshold: float = 100.0) -> bool:
    img = Image.open(image_path)
    if is_blurry(img, blur_threshold):
        return True
    if check_brightness(img) != "normal":
        return True
    return is_low_contrast(img)


def _filter_frames(
    frames: list[dict],
    blur_threshold: float = 100.0,
    dedup_threshold: float = 0.05,
) -> list[dict]:
    """质量过滤 + 直方图去重。

    第一轮：模糊 / 亮度 / 对比度过滤。
    第二轮：直方图巴氏距离去重。
    """
    # ── 第一轮：质量过滤 ──
    quality_frames: list[dict] = []
    for frame in frames:
        try:
            rejected = _opencv_rejects_frame(frame["path"], blur_threshold)
            if rejected is None:
                rejected = _pil_rejects_frame(frame["path"], blur_threshold)
            if rejected:
                continue
            quality_frames.append(frame)
        except Exception:
            # 无法读取或判断时宽容保留
            quality_frames.append(frame)

    # ── 第二轮：直方图去重 ──
    return _deduplicate_frames(quality_frames, dedup_threshold)


def _extract_fixed_interval(
    video_path: str,
    output_dir: str,
    interval_sec: int,
    max_frames: int | None = None,
) -> list[dict]:
    """按固定间隔提取帧（保底兜底逻辑）。"""
    total_duration = _get_duration(video_path)
    if total_duration is None or total_duration <= 0:
        return []

    base_name = _sanitize_filename(Path(video_path).stem)
    os.makedirs(output_dir, exist_ok=True)

    actual_interval = interval_sec
    if total_duration < interval_sec:
        actual_interval = max(1, int(total_duration // 2))

    frames: list[dict] = []
    index = 0
    t = 0.0
    while t < total_duration:
        if max_frames is not None and len(frames) >= max_frames:
            break
        index += 1
        frame = _extract_frame_at_time(
            video_path, output_dir, base_name, t, index
        )
        if frame:
            frames.append(frame)
        t += actual_interval

    return frames


# ── 主入口 ────────────────────────────────────────────────────


def extract_frames(
    video_path: str,
    output_dir: str,
    interval_sec: int = 30,
    mode: str = "fixed",
    max_frames: int = 30,
    transcript_segments: list[dict] | None = None,
) -> list[dict]:
    """从视频中智能提帧：场景检测 → 候选帧 → 质量过滤 → 去重 → 保底。

    mode 语义：
    - ``fixed``  : 按固定间隔 + max_frames 直接提帧，**跳过** scene detect（快速）
    - ``auto``   : scene detect + transcript keywords 智能提帧（严格遵守 max_frames）
    - ``disabled``: 不提帧，返回空列表

    Args:
        video_path: 视频文件路径
        output_dir: 帧输出目录
        interval_sec: 提取间隔（秒），默认 30。为 0 时直接返回空列表
        mode: 提帧模式（fixed / auto / disabled）
        max_frames: 最多保留的帧数（fixed / auto 均为硬上限）
        transcript_segments: 转录片段列表，auto 模式下用于关键词时间点融合

    Returns:
        帧字典列表，每项包含：
        - path: 绝对路径
        - filename: 文件名（如 frame_course_0001.jpg）
        - timestamp_sec: 对应视频时间点（秒）
    """
    if interval_sec <= 0 or mode == "disabled":
        return []

    os.makedirs(output_dir, exist_ok=True)

    # ── fixed 模式：直接按固定间隔 + max_frames，跳过耗时的 scene detect ──
    if mode == "fixed":
        return _extract_fixed_interval(
            video_path, output_dir, interval_sec, max_frames=max_frames
        )

    # ── auto 模式：scene detect → 候选帧 → 质量过滤 → 去重 ──
    total_duration = _get_duration(video_path)
    if total_duration is None or total_duration <= 0:
        logger.warning("无法获取视频时长，跳过帧提取")
        return []

    logger.info("正在检测场景切分点...")
    scene_times = _detect_scenes(video_path)
    logger.info("检测到 %d 个场景切分点", len(scene_times))

    visual_times = _generate_auto_candidate_times(
        scene_times,
        total_duration,
        max_frames=max_frames,
    )
    candidate_times = _merge_candidate_times(
        visual_times,
        transcript_segments,
        total_duration,
        max_frames=max_frames,
    )

    # 无候选帧（无场景信息）→ 回退到固定间隔
    if not candidate_times:
        logger.info("无场景信息，回退到固定间隔提帧")
        return _extract_fixed_interval(
            video_path, output_dir, interval_sec, max_frames=max_frames
        )

    # 提取候选帧
    base_name = _sanitize_filename(Path(video_path).stem)
    raw_frames: list[dict] = []
    for i, t in enumerate(candidate_times):
        frame = _extract_frame_at_time(
            video_path, output_dir, base_name, t, i + 1
        )
        if frame:
            raw_frames.append(frame)

    if not raw_frames:
        logger.warning("候选帧提取失败，回退到固定间隔提帧")
        return _extract_fixed_interval(
            video_path, output_dir, interval_sec, max_frames=max_frames
        )

    # 质量过滤 + 去重
    filtered_frames = _filter_frames(raw_frames)

    # 保底：智能提帧数极低（不足3张）时回退到固定间隔
    min_frames = max(1, int(total_duration / interval_sec * 0.1))
    if len(filtered_frames) < min_frames:
        logger.info(
            "智能提帧仅 %d 张（最低 %d 张），回退到固定间隔提帧",
            len(filtered_frames),
            min_frames,
        )
        return _extract_fixed_interval(
            video_path, output_dir, interval_sec, max_frames=max_frames
        )

    logger.info("帧提取完成: %d 张", len(filtered_frames))
    return filtered_frames
