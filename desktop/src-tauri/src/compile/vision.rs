/// Lightweight frame analysis — no ML dependencies.
///
/// Uses the `image` crate to extract visual features from sampled frames:
/// average brightness, dominant colors, blur detection, edge density.
/// Generates human-readable descriptions from these statistics.
///
/// This powers the Local Draft mode when no GGUF model is available.

use image::GenericImageView;

/// Visual features extracted from a single frame.
#[derive(Debug, Clone)]
pub struct FrameFeatures {
    #[allow(dead_code)]
    pub index: u32,
    pub timestamp_sec: f64,
    /// Average brightness (0.0 = black, 1.0 = white).
    pub brightness: f32,
    /// Dominant color name (e.g. "blue", "warm", "neutral").
    pub dominant_color: &'static str,
    /// Edge density (0.0 = no edges, 1.0 = full of edges/text).
    #[allow(dead_code)]
    pub edge_density: f32,
    /// Estimated blur level (0.0 = sharp, 1.0 = very blurry).
    #[allow(dead_code)]
    pub blur: f32,
    /// Whether this frame is mostly text/UI (high edge density, low color variance).
    pub is_text_heavy: bool,
}

/// Scene-level summary combining multiple frames.
#[derive(Debug, Clone)]
pub struct SceneSummary {
    pub start_sec: f64,
    pub end_sec: f64,
    #[allow(dead_code)]
    pub frame_count: usize,
    #[allow(dead_code)]
    pub avg_brightness: f32,
    pub dominant_mood: &'static str,
    pub description: String,
}

/// Analyze a batch of frames and return per-frame features.
pub fn analyze_frames(frames: &[crate::compile::Frame]) -> Vec<FrameFeatures> {
    frames.iter().map(analyze_frame).collect()
}

fn analyze_frame(frame: &crate::compile::Frame) -> FrameFeatures {
    let img = match image::load_from_memory(&frame.data) {
        Ok(img) => img,
        Err(_) => {
            return FrameFeatures {
                index: frame.index,
                timestamp_sec: frame.timestamp_sec,
                brightness: 0.5,
                dominant_color: "unknown",
                edge_density: 0.0,
                blur: 0.5,
                is_text_heavy: false,
            };
        }
    };

    let (_w, _h) = img.dimensions();
    let small = img.resize_exact(64, 64, image::imageops::FilterType::Nearest);
    let pixels: Vec<[u8; 4]> = small.pixels().map(|p| p.2 .0).collect();

    // --- Brightness ---
    let brightness: f32 = pixels.iter().map(|p| {
        0.299 * p[0] as f32 + 0.587 * p[1] as f32 + 0.114 * p[2] as f32
    }).sum::<f32>() / pixels.len() as f32 / 255.0;

    // --- Dominant color ---
    let dominant_color = classify_color(&pixels);

    // --- Edge density ---
    let edge_density = compute_edge_density(&small);

    // --- Blur estimate ---
    let blur = estimate_blur(&small);

    // --- Text-heavy heuristic ---
    let is_text_heavy = edge_density > 0.25 && brightness > 0.3 && blur < 0.3;

    FrameFeatures {
        index: frame.index,
        timestamp_sec: frame.timestamp_sec,
        brightness,
        dominant_color,
        edge_density,
        blur,
        is_text_heavy,
    }
}

/// Group frames into scenes based on pHash distance and variance.
pub fn group_scenes(frames: &[crate::compile::Frame]) -> Vec<SceneSummary> {
    if frames.is_empty() {
        return vec![];
    }

    let features = analyze_frames(frames);
    let mut scenes: Vec<Vec<(&crate::compile::Frame, &FrameFeatures)>> = Vec::new();
    let mut current: Vec<(&crate::compile::Frame, &FrameFeatures)> = Vec::new();

    for (frame, feat) in frames.iter().zip(features.iter()) {
        if current.is_empty() {
            current.push((frame, feat));
            continue;
        }
        let prev = current.last().unwrap();
        let phash_dist = prev.0.phash.abs_diff(frame.phash);
        let var_diff = (prev.0.variance - frame.variance).abs();

        // Scene boundary: large pHash change OR big variance shift
        if phash_dist > 15 || var_diff > 20.0 {
            scenes.push(std::mem::take(&mut current));
        }
        current.push((frame, feat));
    }
    if !current.is_empty() {
        scenes.push(current);
    }

    scenes.iter().map(|scene| {
        let start_sec = scene.first().unwrap().0.timestamp_sec;
        let end_sec = scene.last().unwrap().0.timestamp_sec;
        let avg_brightness: f32 = scene.iter().map(|(_, f)| f.brightness).sum::<f32>() / scene.len() as f32;
        let text_heavy_count = scene.iter().filter(|(_, f)| f.is_text_heavy).count();
        let dominant_mood = classify_mood(avg_brightness, text_heavy_count > scene.len() / 2);
        let description = describe_scene(scene, avg_brightness, dominant_mood);

        SceneSummary {
            start_sec,
            end_sec,
            frame_count: scene.len(),
            avg_brightness,
            dominant_mood,
            description,
        }
    }).collect()
}

// ─── Internal helpers ───────────────────────────────────────────────

fn classify_color(pixels: &[[u8; 4]]) -> &'static str {
    let mut r_sum = 0u64;
    let mut g_sum = 0u64;
    let mut b_sum = 0u64;
    for p in pixels {
        r_sum += p[0] as u64;
        g_sum += p[1] as u64;
        b_sum += p[2] as u64;
    }
    let n = pixels.len() as f32;
    let r = r_sum as f32 / n;
    let g = g_sum as f32 / n;
    let b = b_sum as f32 / n;

    let max = r.max(g).max(b);
    let min = r.min(g).min(b);
    let saturation = if max > 0.0 { (max - min) / max } else { 0.0 };

    if saturation < 0.08 {
        if max > 200.0 { "white" } else if max < 50.0 { "black" } else { "gray" }
    } else if r > g && r > b && r > 150.0 {
        "reddish"
    } else if g > r && g > b && g > 150.0 {
        "greenish"
    } else if b > r && b > g && b > 150.0 {
        "bluish"
    } else if r > 150.0 && g > 100.0 && b < 100.0 {
        "warm"
    } else if b > 100.0 && g > 100.0 && r < 150.0 {
        "cool"
    } else {
        "neutral"
    }
}

fn compute_edge_density(img: &image::DynamicImage) -> f32 {
    let gray = img.to_luma8();
    let (w, h) = gray.dimensions();
    if w < 2 || h < 2 {
        return 0.0;
    }
    let mut edge_count = 0u32;
    let total = (w - 1) * (h - 1);
    for y in 0..h - 1 {
        for x in 0..w - 1 {
            let tl = gray.get_pixel(x, y).0[0] as i16;
            let tr = gray.get_pixel(x + 1, y).0[0] as i16;
            let bl = gray.get_pixel(x, y + 1).0[0] as i16;
            let br = gray.get_pixel(x + 1, y + 1).0[0] as i16;
            let gx = (tr - tl + br - bl) / 2;
            let gy = (bl - tl + br - tr) / 2;
            let mag = ((gx * gx + gy * gy) as f32).sqrt();
            if mag > 30.0 {
                edge_count += 1;
            }
        }
    }
    edge_count as f32 / total as f32
}

fn estimate_blur(img: &image::DynamicImage) -> f32 {
    let gray = img.to_luma8();
    let (w, h) = gray.dimensions();
    if w < 2 || h < 2 {
        return 0.5;
    }
    let mut sum = 0.0f64;
    let mut count = 0u64;
    for y in 0..h - 1 {
        for x in 0..w - 1 {
            let c = gray.get_pixel(x, y).0[0] as f64;
            let right = gray.get_pixel(x + 1, y).0[0] as f64;
            let down = gray.get_pixel(x, y + 1).0[0] as f64;
            sum += (c - right).abs() + (c - down).abs();
            count += 1;
        }
    }
    let avg_diff = sum / count as f64;
    // Normalize: 0-30 range → 0.0-1.0 blur (higher avg_diff = sharper)
    (1.0 - (avg_diff / 30.0).min(1.0)) as f32
}

fn classify_mood(brightness: f32, is_text_heavy: bool) -> &'static str {
    if is_text_heavy {
        "textual"
    } else if brightness > 0.7 {
        "bright"
    } else if brightness > 0.4 {
        "neutral"
    } else if brightness > 0.2 {
        "dim"
    } else {
        "dark"
    }
}

fn describe_scene(
    scene: &[(&crate::compile::Frame, &FrameFeatures)],
    _avg_brightness: f32,
    mood: &str,
) -> String {
    let duration = scene.last().unwrap().0.timestamp_sec - scene.first().unwrap().0.timestamp_sec;
    let colors: Vec<&str> = scene.iter().map(|(_, f)| f.dominant_color).collect();
    let unique_colors = {
        let mut v: Vec<&str> = colors.clone();
        v.sort();
        v.dedup();
        v
    };

    let mut parts: Vec<String> = Vec::new();

    match mood {
        "textual" => parts.push("画面以文字/UI为主".to_string()),
        "bright" => parts.push("明亮场景".to_string()),
        "neutral" => parts.push("正常光照场景".to_string()),
        "dim" => parts.push("较暗场景".to_string()),
        "dark" => parts.push("黑暗场景".to_string()),
        _ => {}
    }

    if !unique_colors.is_empty() && unique_colors != ["neutral"] {
        parts.push(format!("主色调: {}", unique_colors.join("/")));
    }

    if duration > 5.0 {
        parts.push(format!("持续约 {:.0}s", duration));
    }

    if parts.is_empty() {
        "视频片段".to_string()
    } else {
        parts.join("，")
    }
}