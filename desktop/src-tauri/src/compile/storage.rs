/// Immutable Storage — Pass 4.
///
/// Stores compiled VideoCapsules with composite key (source_hash, version).
/// Versions are never overwritten — each compile creates a new version.
///
/// Current implementation: file-based JSON store.
/// Design allows future SQLite/pgvector backend via CapsuleStore trait.

use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

use chrono::Utc;
use sha2::{Digest, Sha256};
use uuid::Uuid;

use crate::compile::{CompileMode, Evidence, EvidenceType, VideoCapsule};

// ---------------------------------------------------------------------------
// CapsuleStore trait — abstraction for future backends
// ---------------------------------------------------------------------------

/// Storage backend for VideoCapsules.
pub trait CapsuleStore: Send + Sync {
    /// Insert a new capsule version. Returns the capsule_id on success.
    fn insert(&mut self, capsule: VideoCapsule) -> Result<String, String>;

    /// Retrieve a specific version of a capsule.
    fn get(&self, source_hash: &str, version: u32) -> Result<VideoCapsule, String>;

    /// List all available versions for a source.
    fn list_versions(&self, source_hash: &str) -> Result<Vec<VersionInfo>, String>;

    /// Get the latest version for a source.
    #[allow(dead_code)]
    fn latest(&self, source_hash: &str) -> Result<VideoCapsule, String> {
        let versions = self.list_versions(source_hash)?;
        let max_v = versions.iter().map(|v| v.version).max().unwrap_or(0);
        if max_v == 0 {
            return Err(format!("no versions found for source_hash {source_hash}"));
        }
        self.get(source_hash, max_v)
    }

    /// Check if a source_hash + version already exists.
    #[allow(dead_code)]
    fn exists(&self, source_hash: &str, version: u32) -> bool;
}

/// Version metadata (for list_versions response).
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct VersionInfo {
    pub version: u32,
    pub created_at: String,
    pub model_used: String,
    pub compilation_mode: CompileMode,
    pub total_duration: f32,
    pub evidence_count: usize,
}

// ---------------------------------------------------------------------------
// FileCapsuleStore — file-based JSON storage
// ---------------------------------------------------------------------------

/// File-based capsule storage.
///
/// Layout:
///   {base_dir}/{source_hash}/v{version}.json
///   {base_dir}/{source_hash}/versions.json  (index)
pub struct FileCapsuleStore {
    base_dir: PathBuf,
}

impl FileCapsuleStore {
    /// Create a new file-based store rooted at `base_dir`.
    pub fn new(base_dir: PathBuf) -> Self {
        Self { base_dir }
    }

    /// Compute the directory for a given source_hash.
    fn source_dir(&self, source_hash: &str) -> PathBuf {
        self.base_dir.join(source_hash)
    }

    /// Compute the file path for a specific version.
    fn version_path(&self, source_hash: &str, version: u32) -> PathBuf {
        self.source_dir(source_hash).join(format!("v{version}.json"))
    }

    /// Compute the index path for a source.
    fn index_path(&self, source_hash: &str) -> PathBuf {
        self.source_dir(source_hash).join("versions.json")
    }

    /// Read the versions index for a source.
    fn read_index(&self, source_hash: &str) -> Vec<VersionInfo> {
        let path = self.index_path(source_hash);
        fs::read_to_string(&path)
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_default()
    }

    /// Write the versions index for a source.
    fn write_index(&self, source_hash: &str, versions: &[VersionInfo]) -> Result<(), String> {
        let path = self.index_path(source_hash);
        let dir = path.parent().unwrap();
        fs::create_dir_all(dir).map_err(|e| format!("failed to create storage dir: {e}"))?;
        let json = serde_json::to_string_pretty(versions)
            .map_err(|e| format!("failed to serialize index: {e}"))?;
        atomic_write(&path, &json)
    }
}

impl CapsuleStore for FileCapsuleStore {
    fn insert(&mut self, capsule: VideoCapsule) -> Result<String, String> {
        let source_hash = &capsule.source_hash;
        let version = capsule.version;
        let path = self.version_path(source_hash, version);

        // Ensure the directory exists
        let dir = path.parent().unwrap();
        fs::create_dir_all(dir).map_err(|e| format!("failed to create capsule dir: {e}"))?;

        // Check for collision (shouldn't happen if version is managed correctly)
        if path.exists() {
            return Err(format!(
                "capsule {source_hash} v{version} already exists — versions are immutable"
            ));
        }

        // Write capsule
        let json = serde_json::to_string_pretty(&capsule)
            .map_err(|e| format!("failed to serialize capsule: {e}"))?;
        atomic_write(&path, &json)?;

        // Update index
        let mut versions = self.read_index(source_hash);
        versions.push(VersionInfo {
            version,
            created_at: capsule.processed_at.clone(),
            model_used: capsule.model_used.clone(),
            compilation_mode: capsule.compilation_mode,
            total_duration: capsule.total_duration,
            evidence_count: capsule.evidences.len(),
        });
        self.write_index(source_hash, &versions)?;

        Ok(capsule.capsule_id)
    }

    fn get(&self, source_hash: &str, version: u32) -> Result<VideoCapsule, String> {
        let path = self.version_path(source_hash, version);
        let json = fs::read_to_string(&path)
            .map_err(|e| format!("capsule {source_hash} v{version} not found: {e}"))?;
        serde_json::from_str(&json)
            .map_err(|e| format!("failed to parse capsule {source_hash} v{version}: {e}"))
    }

    fn list_versions(&self, source_hash: &str) -> Result<Vec<VersionInfo>, String> {
        Ok(self.read_index(source_hash))
    }

    fn exists(&self, source_hash: &str, version: u32) -> bool {
        self.version_path(source_hash, version).exists()
    }
}

// ---------------------------------------------------------------------------
// Capsule builder — helper to create VideoCapsule from compile results
// ---------------------------------------------------------------------------

/// Build a VideoCapsule from compile outputs aggregated across chunks.
pub struct CapsuleBuilder {
    source_hash: String,
    version: u32,
    model_used: String,
    total_duration: f32,
    compilation_mode: CompileMode,
    evidences: Vec<Evidence>,
    chunk_summaries: Vec<(u32, String)>,
}

impl CapsuleBuilder {
    pub fn new(
        source_hash: String,
        model_used: String,
        total_duration: f32,
        mode: CompileMode,
    ) -> Self {
        Self {
            source_hash,
            version: 0, // set by store
            model_used,
            total_duration,
            compilation_mode: mode,
            evidences: Vec::new(),
            chunk_summaries: Vec::new(),
        }
    }

    /// Add events from one compiled chunk.
    pub fn add_chunk(
        &mut self,
        chunk_sequence: u32,
        events: Vec<crate::compile::RawEvent>,
        chunk_summary: &str,
        frame_index_map: &HashMap<u32, f64>,
        calibrated_confidence: f32,
    ) {
        let version = self.version; // frozen at build time
        let source_hash = self.source_hash.clone();

        for event in events {
            // Convert frame indexes to physical timestamps
            let start_sec = event
                .event_frame_indexes
                .first()
                .and_then(|idx| frame_index_map.get(idx))
                .copied()
                .unwrap_or(0.0) as f32;
            let end_sec = event
                .event_frame_indexes
                .last()
                .and_then(|idx| frame_index_map.get(idx))
                .copied()
                .unwrap_or(start_sec as f64) as f32;

            let evidence_type = match event.event_type.as_str() {
                "fact" => EvidenceType::Fact,
                "procedure" => EvidenceType::Procedure,
                "concept" => EvidenceType::Concept,
                "failure" => EvidenceType::Failure,
                "verification" => EvidenceType::Verification,
                _ => EvidenceType::Concept,
            };

            self.evidences.push(Evidence {
                id: Uuid::new_v4().to_string(),
                source_hash: source_hash.clone(),
                version,
                chunk_sequence,
                content: event.description,
                timestamp_start_sec: start_sec,
                timestamp_end_sec: end_sec,
                evidence_type,
                speaker: event.speaker,
                confidence: calibrated_confidence,
                visual_context: event.title,
                prev_chunk_summary_hash: None, // set by bridge
                is_redundant: false,
            });
        }

        self.chunk_summaries
            .push((chunk_sequence, chunk_summary.to_string()));
    }

    /// Build the final capsule, setting version and generating global summary.
    pub fn build(self, version: u32) -> VideoCapsule {
        let global_summary = self
            .chunk_summaries
            .iter()
            .map(|(seq, summary)| format!("[Chunk {seq}] {summary}"))
            .collect::<Vec<_>>()
            .join("\n\n");

        let capsule_id = format!("{}_{}", self.source_hash, version);

        VideoCapsule {
            capsule_id,
            source_hash: self.source_hash,
            version,
            total_duration: self.total_duration,
            processed_at: Utc::now().to_rfc3339(),
            model_used: self.model_used,
            evidences: self.evidences,
            global_summary,
            compilation_mode: self.compilation_mode,
        }
    }
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

/// Compute SHA-256 hex digest of a file's content.
#[allow(dead_code)]
pub fn file_hash(path: &Path) -> Result<String, String> {
    let data = fs::read(path).map_err(|e| format!("failed to read file for hashing: {e}"))?;
    let mut hasher = Sha256::new();
    hasher.update(&data);
    Ok(format!("{:x}", hasher.finalize()))
}

/// Atomically write string content to a file (write to temp, then rename).
fn atomic_write(path: &Path, content: &str) -> Result<(), String> {
    let dir = path.parent().unwrap();
    let tmp_name = format!(
        ".tmp_{}_{}",
        path.file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("unknown"),
        std::process::id()
    );
    let tmp_path = dir.join(&tmp_name);

    fs::write(&tmp_path, content).map_err(|e| format!("failed to write temp file: {e}"))?;

    fs::rename(&tmp_path, path).map_err(|e| format!("failed to rename temp file: {e}"))?;

    Ok(())
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::compile::RawEvent;

    fn temp_store() -> (FileCapsuleStore, PathBuf) {
        let dir = std::env::temp_dir().join(format!("vn-test-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        (FileCapsuleStore::new(dir.clone()), dir)
    }

    #[test]
    fn test_file_store_insert_and_get() {
        let (mut store, _dir) = temp_store();

        let capsule = VideoCapsule {
            capsule_id: "abc123_1".to_string(),
            source_hash: "abc123".to_string(),
            version: 1,
            total_duration: 120.0,
            processed_at: Utc::now().to_rfc3339(),
            model_used: "gpt-4o".to_string(),
            evidences: vec![],
            global_summary: "test".to_string(),
            compilation_mode: CompileMode::CloudPrecision,
        };

        let id = store.insert(capsule.clone()).unwrap();
        assert_eq!(id, "abc123_1");

        let retrieved = store.get("abc123", 1).unwrap();
        assert_eq!(retrieved.global_summary, "test");
        assert_eq!(retrieved.version, 1);
    }

    #[test]
    fn test_file_store_immutable_rejects_overwrite() {
        let (mut store, _dir) = temp_store();

        let capsule = VideoCapsule {
            capsule_id: "abc_1".to_string(),
            source_hash: "abc".to_string(),
            version: 1,
            total_duration: 10.0,
            processed_at: Utc::now().to_rfc3339(),
            model_used: "gpt-4o".to_string(),
            evidences: vec![],
            global_summary: "v1".to_string(),
            compilation_mode: CompileMode::CloudPrecision,
        };

        store.insert(capsule).unwrap();

        let duplicate = VideoCapsule {
            capsule_id: "abc_1".to_string(),
            source_hash: "abc".to_string(),
            version: 1,
            total_duration: 10.0,
            processed_at: Utc::now().to_rfc3339(),
            model_used: "gpt-4o".to_string(),
            evidences: vec![],
            global_summary: "v1-overwrite".to_string(),
            compilation_mode: CompileMode::CloudPrecision,
        };

        let result = store.insert(duplicate);
        assert!(result.is_err(), "should reject overwrite of immutable version");
    }

    #[test]
    fn test_file_store_list_versions() {
        let (mut store, _dir) = temp_store();

        for v in 1..=3 {
            let capsule = VideoCapsule {
                capsule_id: format!("abc_{v}"),
                source_hash: "abc".to_string(),
                version: v,
                total_duration: 10.0,
                processed_at: Utc::now().to_rfc3339(),
                model_used: "gpt-4o".to_string(),
                evidences: vec![],
                global_summary: format!("v{v}"),
                compilation_mode: CompileMode::CloudPrecision,
            };
            store.insert(capsule).unwrap();
        }

        let versions = store.list_versions("abc").unwrap();
        assert_eq!(versions.len(), 3);

        let latest = store.latest("abc").unwrap();
        assert_eq!(latest.version, 3);
    }

    #[test]
    fn test_file_store_exists() {
        let (mut store, _dir) = temp_store();

        let capsule = VideoCapsule {
            capsule_id: "abc_1".to_string(),
            source_hash: "abc".to_string(),
            version: 1,
            total_duration: 10.0,
            processed_at: Utc::now().to_rfc3339(),
            model_used: "gpt-4o".to_string(),
            evidences: vec![],
            global_summary: "test".to_string(),
            compilation_mode: CompileMode::CloudPrecision,
        };
        store.insert(capsule).unwrap();

        assert!(store.exists("abc", 1));
        assert!(!store.exists("abc", 99));
        assert!(!store.exists("nonexistent", 1));
    }

    #[test]
    fn test_capsule_builder() {
        let mut builder = CapsuleBuilder::new(
            "abc123".to_string(),
            "gpt-4o".to_string(),
            120.0,
            CompileMode::CloudPrecision,
        );

        let mut frame_map = HashMap::new();
        frame_map.insert(0, 0.0);
        frame_map.insert(3, 3.0);
        frame_map.insert(7, 7.5);

        let events = vec![
            RawEvent {
                title: "Intro".to_string(),
                event_frame_indexes: vec![0, 3],
                description: "Speaker introduces the topic.".to_string(),
                event_type: "concept".to_string(),
                speaker: Some("Alice".to_string()),
                confidence: 0.9,
            },
            RawEvent {
                title: "Demo".to_string(),
                event_frame_indexes: vec![7, 7],
                description: "Live coding demonstration.".to_string(),
                event_type: "procedure".to_string(),
                speaker: None,
                confidence: 0.85,
            },
        ];

        builder.add_chunk(0, events, "Introduction to Rust", &frame_map, 0.87);

        let capsule = builder.build(1);
        assert_eq!(capsule.capsule_id, "abc123_1");
        assert_eq!(capsule.version, 1);
        assert_eq!(capsule.evidences.len(), 2);
        assert_eq!(capsule.evidences[0].timestamp_start_sec, 0.0);
        assert_eq!(capsule.evidences[0].timestamp_end_sec, 3.0);
        assert_eq!(capsule.evidences[1].timestamp_start_sec, 7.5);
        assert!(capsule.global_summary.contains("Introduction to Rust"));
    }

    #[test]
    fn test_file_hash() {
        let dir = std::env::temp_dir().join(format!("vn-test-filehash-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let path = dir.join("test.txt");
        fs::write(&path, "hello world").unwrap();
        let hash = file_hash(&path).unwrap();
        assert_eq!(hash.len(), 64);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_atomic_write() {
        let dir = std::env::temp_dir().join(format!("vn-test-atomic-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let path = dir.join("capsule.json");

        atomic_write(&path, r#"{"test": true}"#).unwrap();
        assert!(path.exists());
        let content = fs::read_to_string(&path).unwrap();
        assert_eq!(content, r#"{"test": true}"#);
        let _ = fs::remove_dir_all(&dir);
    }
}