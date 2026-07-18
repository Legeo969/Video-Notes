/// Context Bridge — cross-chunk context stitching.
///
/// Ensures continuity between video chunk compilations by managing sequence
/// numbers and providing the previous chunk's summary as context for the next.
use sha2::{Digest, Sha256};

/// Metadata that bridges one chunk to the next.
#[derive(Debug, Clone)]
pub struct ChunkContext {
    /// Sequence number of this chunk (0-indexed).
    pub chunk_sequence: u32,
    /// Total chunks in the compilation.
    pub total_chunks: u32,
    /// The previous chunk's summary text (injected into the next prompt).
    pub prev_chunk_summary: String,
}

impl ChunkContext {
    /// Create context for the first chunk (no previous context).
    pub fn first(total_chunks: u32) -> Self {
        Self {
            chunk_sequence: 0,
            total_chunks,
            prev_chunk_summary: String::new(),
        }
    }

    /// Advance to the next chunk, carrying the given summary forward.
    pub fn advance(&self, current_summary: &str) -> Self {
        Self {
            chunk_sequence: self.chunk_sequence + 1,
            total_chunks: self.total_chunks,
            prev_chunk_summary: current_summary.to_string(),
        }
    }
}

/// Compute SHA-256 hex digest of a string.
pub fn sha256_hex(text: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(text.as_bytes());
    format!("{:x}", hasher.finalize())
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_chunk_context_first() {
        let ctx = ChunkContext::first(3);
        assert_eq!(ctx.chunk_sequence, 0);
        assert_eq!(ctx.total_chunks, 3);
        assert!(ctx.prev_chunk_summary.is_empty());
    }

    #[test]
    fn test_chunk_context_advance() {
        let ctx = ChunkContext::first(2);
        let next = ctx.advance("Introduction to Rust ownership");
        assert_eq!(next.chunk_sequence, 1);
        assert_eq!(next.prev_chunk_summary, "Introduction to Rust ownership");
    }

    #[test]
    fn test_sha256_hex_consistent() {
        let h1 = sha256_hex("hello world");
        let h2 = sha256_hex("hello world");
        assert_eq!(h1, h2);
        assert_eq!(h1.len(), 64); // SHA-256 hex is 64 chars
    }

    #[test]
    fn test_sha256_hex_different_inputs() {
        let h1 = sha256_hex("hello");
        let h2 = sha256_hex("world");
        assert_ne!(h1, h2);
    }
}
