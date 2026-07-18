/// Confidence Calibration — Pass 3.
///
/// Recalculates confidence using a hybrid formula:
///   final = model_score × (0.4 + 0.6 × context_coherence)
///   If context_coherence < 0.3, final *= 0.5 (mark as suspicious)
///
/// Context coherence is computed as cosine similarity between
/// character n-gram embeddings of chunk summaries.
use std::collections::HashMap;

/// Minimum context coherence threshold. Below this, confidence is halved.
const COHERENCE_THRESHOLD: f32 = 0.3;

/// Calibrate a model's raw confidence score using context coherence.
///
/// * `model_score` — raw confidence from the MLLM (0.0–1.0)
/// * `chunk_summary` — summary of the current chunk
/// * `prev_summary` — summary of the immediately preceding chunk
/// * `next_summary` — summary of the immediately following chunk (empty if last)
///
/// Returns the calibrated confidence score (0.0–1.0).
pub fn calibrate_confidence(
    model_score: f32,
    chunk_summary: &str,
    prev_summary: &str,
    next_summary: &str,
) -> f32 {
    let embed = ngram_embedding(chunk_summary);
    let prev_sim = if prev_summary.is_empty() {
        0.5 // neutral if no context
    } else {
        cosine_similarity(&embed, &ngram_embedding(prev_summary))
    };
    let next_sim = if next_summary.is_empty() {
        0.5 // neutral if no context
    } else {
        cosine_similarity(&embed, &ngram_embedding(next_summary))
    };

    let context_coherence = (prev_sim + next_sim) / 2.0;

    // Coherence may lower confidence, but must never inflate a model's own
    // confidence estimate. This keeps low-confidence observations reviewable.
    let raw = model_score * (0.4 + 0.6 * context_coherence);

    // Hard truncation: if context coherence is extremely poor, halve the score
    if context_coherence < COHERENCE_THRESHOLD {
        (raw * 0.5).clamp(0.0, 1.0)
    } else {
        raw.clamp(0.0, 1.0)
    }
}

// ---------------------------------------------------------------------------
// Character n-gram embedding
// ---------------------------------------------------------------------------

/// Build a sparse vector of character trigram counts from text.
///
/// This is a simple embedding that captures structural similarity between
/// short texts (like summaries). Works well for Chinese text where character
/// n-grams are meaningful.
fn ngram_embedding(text: &str) -> HashMap<String, f32> {
    let mut map = HashMap::new();
    let chars: Vec<char> = text.chars().collect();

    // Use trigrams (3-char sliding window)
    for window in chars.windows(3) {
        let key: String = window.iter().collect();
        *map.entry(key).or_insert(0.0f32) += 1.0;
    }

    // If text is too short for trigrams, use bigrams
    if map.is_empty() {
        for window in chars.windows(2) {
            let key: String = window.iter().collect();
            *map.entry(key).or_insert(0.0f32) += 1.0;
        }
    }

    // If still empty, use unigrams
    if map.is_empty() {
        for c in chars {
            let key = c.to_string();
            *map.entry(key).or_insert(0.0f32) += 1.0;
        }
    }

    // Normalize: TF = count / total (to avoid length bias)
    let total: f32 = map.values().sum();
    if total > 0.0 {
        for value in map.values_mut() {
            *value /= total;
        }
    }

    map
}

/// Compute cosine similarity between two sparse vectors.
fn cosine_similarity(a: &HashMap<String, f32>, b: &HashMap<String, f32>) -> f32 {
    let mut dot_product = 0.0f32;
    let mut mag_a = 0.0f32;
    let mut mag_b = 0.0f32;

    for (key, val_a) in a {
        mag_a += val_a * val_a;
        if let Some(val_b) = b.get(key) {
            dot_product += val_a * val_b;
        }
    }

    for val_b in b.values() {
        mag_b += val_b * val_b;
    }

    let denom = mag_a.sqrt() * mag_b.sqrt();
    if denom < 1e-10 {
        0.0 // avoid division by zero
    } else {
        (dot_product / denom).clamp(0.0, 1.0)
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_calibrate_high_coherence() {
        // Same summary → high coherence
        let score = calibrate_confidence(
            0.9,
            "Rust ownership system",
            "Rust ownership system",
            "Rust ownership system",
        );
        // Should be close to 0.9 (model score dominates when coherence is high)
        assert!(
            score > 0.7,
            "expected high confidence for coherent context, got {score}"
        );
        assert!(score <= 1.0, "confidence must not exceed 1.0");
    }

    #[test]
    fn test_calibrate_low_coherence_halves() {
        // Very different summaries → low coherence → halved
        let score = calibrate_confidence(
            0.9,
            "量子计算的基本原理",
            "如何煮咖啡",
            "Windows 注册表修复",
        );
        // Coherence should be very low (< 0.3), so confidence gets halved
        assert!(
            score < 0.5,
            "expected halved confidence for incoherent context, got {score}"
        );
    }

    #[test]
    fn test_calibrate_low_model_score() {
        let score = calibrate_confidence(0.2, "same", "same", "same");
        assert!(
            score < 0.5,
            "low model score should result in low confidence"
        );
    }

    #[test]
    fn test_calibrate_clamps() {
        // Test that output is always in [0, 1]
        let test_scores = [0.0, 0.15, 0.3, 0.5, 0.7, 0.9, 1.0];
        for &ms in &test_scores {
            let s = calibrate_confidence(ms, "test", "test", "test");
            assert!((0.0..=1.0).contains(&s), "confidence out of range: {s}");
        }
    }

    #[test]
    fn test_ngram_embedding_length_normalized() {
        let short = ngram_embedding("hello world");
        let long = ngram_embedding("hello world and more text here");
        // Both should be normalized (sum of values ≈ 1.0)
        let short_sum: f32 = short.values().sum();
        let long_sum: f32 = long.values().sum();
        assert!(
            (short_sum - 1.0).abs() < 0.01,
            "short not normalized: {short_sum}"
        );
        assert!(
            (long_sum - 1.0).abs() < 0.01,
            "long not normalized: {long_sum}"
        );
    }

    #[test]
    fn test_cosine_similarity_identical() {
        let a = ngram_embedding("Rust ownership");
        let sim = cosine_similarity(&a, &a);
        assert!(
            (sim - 1.0).abs() < 0.01,
            "identical embeddings should have similarity 1.0"
        );
    }

    #[test]
    fn test_cosine_similarity_orthogonal() {
        // Empty vs anything → 0
        let a: HashMap<String, f32> = HashMap::new();
        let b = ngram_embedding("something");
        let sim = cosine_similarity(&a, &b);
        assert!(
            (sim - 0.0).abs() < 0.01,
            "empty vector should have 0 similarity"
        );
    }

    #[test]
    fn test_ngram_embedding_chinese() {
        let emb = ngram_embedding("Rust 所有权系统");
        assert!(!emb.is_empty(), "Chinese text should produce n-grams");
        // Verify trigrams of Chinese chars exist
        let has_chinese_ngram = emb
            .keys()
            .any(|k| k.contains("所有权") || k.contains("权系"));
        assert!(
            has_chinese_ngram,
            "should contain Chinese character trigrams"
        );
    }
}
