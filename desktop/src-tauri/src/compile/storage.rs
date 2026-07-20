//! Immutable file-backed capsule storage.
//!
//! The JSON capsule files are the source of truth. `versions.json` is only a
//! rebuildable cache, so a crash cannot orphan an otherwise valid version.

use std::collections::HashMap;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::thread;
use std::time::{Duration, SystemTime};

use chrono::Utc;
use sha2::{Digest, Sha256};
use uuid::Uuid;

use crate::compile::{CompileMode, Evidence, EvidenceType, VideoCapsule, IR_SCHEMA_VERSION};

pub trait CapsuleStore: Send + Sync {
    fn insert(&mut self, capsule: VideoCapsule) -> Result<String, String>;
    fn get(&self, source_hash: &str, version: u32) -> Result<VideoCapsule, String>;
    fn list_versions(&self, source_hash: &str) -> Result<Vec<VersionInfo>, String>;
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct VersionInfo {
    pub version: u32,
    pub created_at: String,
    pub model_used: String,
    pub compilation_mode: CompileMode,
    pub total_duration: f32,
    pub evidence_count: usize,
}

pub struct FileCapsuleStore {
    base_dir: PathBuf,
}

impl FileCapsuleStore {
    pub fn new(base_dir: PathBuf) -> Self {
        Self { base_dir }
    }

    pub fn source_dir(&self, source_hash: &str) -> Result<PathBuf, String> {
        validate_source_hash(source_hash)?;
        Ok(self.base_dir.join(source_hash))
    }

    fn version_path(&self, source_hash: &str, version: u32) -> Result<PathBuf, String> {
        Ok(self
            .source_dir(source_hash)?
            .join(format!("v{version}.json")))
    }

    fn reservation_path(&self, source_hash: &str, version: u32) -> Result<PathBuf, String> {
        Ok(self
            .source_dir(source_hash)?
            .join(format!("v{version}.reserve")))
    }

    fn index_path(&self, source_hash: &str) -> Result<PathBuf, String> {
        Ok(self.source_dir(source_hash)?.join("versions.json"))
    }

    pub fn reserve_next_version(&self, source_hash: &str) -> Result<u32, String> {
        let _lock = self.acquire_source_lock(source_hash)?;
        self.cleanup_stale_reservations(source_hash)?;
        let mut max_version = self
            .scan_versions(source_hash)?
            .last()
            .map(|v| v.version)
            .unwrap_or(0);
        let dir = self.source_dir(source_hash)?;
        if let Ok(entries) = fs::read_dir(&dir) {
            for path in entries.flatten().map(|entry| entry.path()) {
                if let Some(version) = parse_version_filename(&path, ".reserve") {
                    max_version = max_version.max(version);
                }
            }
        }
        let version = max_version
            .checked_add(1)
            .ok_or_else(|| "capsule version overflow".to_string())?;
        let path = self.reservation_path(source_hash, version)?;
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&path)
            .map_err(|e| format!("failed to reserve capsule version {version}: {e}"))?;
        writeln!(file, "{}", Utc::now())
            .map_err(|e| format!("failed to write reservation: {e}"))?;
        file.sync_all()
            .map_err(|e| format!("failed to sync reservation: {e}"))?;
        Ok(version)
    }

    pub fn cancel_reservation(&self, source_hash: &str, version: u32) {
        if let Ok(path) = self.reservation_path(source_hash, version) {
            let _ = fs::remove_file(path);
        }
    }

    fn acquire_source_lock(&self, source_hash: &str) -> Result<SourceLock, String> {
        let dir = self.source_dir(source_hash)?;
        fs::create_dir_all(&dir).map_err(|e| format!("failed to create capsule dir: {e}"))?;
        let path = dir.join(".write.lock");
        for _ in 0..250 {
            match OpenOptions::new().write(true).create_new(true).open(&path) {
                Ok(mut file) => {
                    let _ = writeln!(file, "{} {}", std::process::id(), Utc::now());
                    return Ok(SourceLock { path });
                }
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                    if file_is_stale(&path, Duration::from_secs(300)) {
                        let _ = fs::remove_file(&path);
                        continue;
                    }
                    thread::sleep(Duration::from_millis(20));
                }
                Err(error) => return Err(format!("failed to acquire capsule lock: {error}")),
            }
        }
        Err(format!(
            "timed out acquiring storage lock for {source_hash}"
        ))
    }

    fn cleanup_stale_reservations(&self, source_hash: &str) -> Result<(), String> {
        let dir = self.source_dir(source_hash)?;
        let Ok(entries) = fs::read_dir(dir) else {
            return Ok(());
        };
        for path in entries.flatten().map(|entry| entry.path()) {
            if parse_version_filename(&path, ".reserve").is_some()
                && file_is_stale(&path, Duration::from_secs(24 * 60 * 60))
            {
                let _ = fs::remove_file(path);
            }
        }
        Ok(())
    }

    fn scan_versions(&self, source_hash: &str) -> Result<Vec<VersionInfo>, String> {
        let dir = self.source_dir(source_hash)?;
        let Ok(entries) = fs::read_dir(&dir) else {
            return Ok(Vec::new());
        };
        let mut versions = Vec::new();
        for path in entries.flatten().map(|entry| entry.path()) {
            if parse_version_filename(&path, ".json").is_none() {
                continue;
            }
            let Ok(text) = fs::read_to_string(&path) else {
                continue;
            };
            let Ok(capsule) = serde_json::from_str::<VideoCapsule>(&text) else {
                continue;
            };
            versions.push(VersionInfo {
                version: capsule.version,
                created_at: capsule.processed_at,
                model_used: capsule.model_used,
                compilation_mode: capsule.compilation_mode,
                total_duration: capsule.total_duration,
                evidence_count: capsule.evidences.len(),
            });
        }
        versions.sort_by_key(|item| item.version);
        versions.dedup_by_key(|item| item.version);
        Ok(versions)
    }

    fn refresh_index(&self, source_hash: &str) -> Result<(), String> {
        let versions = self.scan_versions(source_hash)?;
        let content = serde_json::to_string_pretty(&versions)
            .map_err(|e| format!("failed to serialize version index: {e}"))?;
        atomic_replace(&self.index_path(source_hash)?, content.as_bytes())
    }
}

impl CapsuleStore for FileCapsuleStore {
    fn insert(&mut self, capsule: VideoCapsule) -> Result<String, String> {
        validate_capsule(&capsule)?;
        let source_hash = capsule.source_hash.clone();
        let version = capsule.version;
        let reservation = self.reservation_path(&source_hash, version)?;
        if !reservation.is_file() {
            return Err(format!(
                "version {version} was not reserved before immutable insert"
            ));
        }

        let content = serde_json::to_vec_pretty(&capsule)
            .map_err(|e| format!("failed to serialize capsule: {e}"))?;
        let path = self.version_path(&source_hash, version)?;
        let temp = path.with_extension(format!("json.tmp-{}", Uuid::new_v4()));
        write_new_file(&temp, &content)?;
        if path.exists() {
            let _ = fs::remove_file(&temp);
            return Err(format!(
                "capsule {source_hash} v{version} already exists; versions are immutable"
            ));
        }
        fs::rename(&temp, &path).map_err(|e| format!("failed to commit capsule: {e}"))?;

        let _lock = self.acquire_source_lock(&source_hash)?;
        let _ = fs::remove_file(reservation);
        // The per-source index is a rebuildable cache. A committed immutable
        // capsule remains successful even if refreshing this cache fails.
        let _ = self.refresh_index(&source_hash);
        Ok(capsule.capsule_id)
    }

    fn get(&self, source_hash: &str, version: u32) -> Result<VideoCapsule, String> {
        let path = self.version_path(source_hash, version)?;
        let text = fs::read_to_string(&path)
            .map_err(|e| format!("capsule {source_hash} v{version} not found: {e}"))?;
        serde_json::from_str(&text)
            .map_err(|e| format!("failed to parse capsule {source_hash} v{version}: {e}"))
    }

    fn list_versions(&self, source_hash: &str) -> Result<Vec<VersionInfo>, String> {
        self.scan_versions(source_hash)
    }
}

struct SourceLock {
    path: PathBuf,
}

impl Drop for SourceLock {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.path);
    }
}

pub struct CapsuleBuilder {
    source_hash: String,
    source_title: String,
    model_used: String,
    total_duration: f32,
    evidences: Vec<Evidence>,
    chunk_summaries: Vec<(u32, String)>,
}

impl CapsuleBuilder {
    pub fn new(
        source_hash: String,
        source_title: String,
        model_used: String,
        total_duration: f32,
    ) -> Self {
        Self {
            source_hash,
            source_title,
            model_used,
            total_duration,
            evidences: Vec::new(),
            chunk_summaries: Vec::new(),
        }
    }

    pub fn add_chunk(
        &mut self,
        chunk_sequence: u32,
        events: Vec<crate::compile::RawEvent>,
        chunk_summary: &str,
        frame_index_map: &HashMap<u32, f64>,
        prev_chunk_summary_hash: Option<String>,
    ) {
        for event in events {
            let start = event
                .event_frame_indexes
                .first()
                .and_then(|index| frame_index_map.get(index))
                .copied();
            let end = event
                .event_frame_indexes
                .last()
                .and_then(|index| frame_index_map.get(index))
                .copied();
            let mut review_reasons = Vec::new();
            let (start_sec, end_sec) = match (start, end) {
                (Some(start), Some(end)) => {
                    if start > end {
                        review_reasons.push("reversed_anchors".to_string());
                    }
                    (start.min(end) as f32, start.max(end) as f32)
                }
                _ => {
                    review_reasons.push("missing_backend_time_anchor".to_string());
                    (0.0, 0.0)
                }
            };
            let evidence_type = parse_evidence_type(&event.event_type);
            let confidence = if evidence_type == EvidenceType::Draft {
                event.confidence.min(0.3)
            } else if review_reasons.is_empty() {
                event.confidence.clamp(0.0, 1.0)
            } else {
                event.confidence.min(0.2)
            };
            if confidence < 0.4 {
                review_reasons.push("low_confidence".to_string());
            }
            if evidence_type == EvidenceType::Draft {
                review_reasons.push("local_draft_requires_cloud_recompile".to_string());
            }

            self.evidences.push(Evidence {
                id: Uuid::new_v4().to_string(),
                source_hash: self.source_hash.clone(),
                version: 0,
                chunk_sequence,
                content: strip_segment_header(&event.description),
                timestamp_start_sec: start_sec,
                timestamp_end_sec: end_sec,
                evidence_type,
                speaker: event.speaker,
                confidence,
                visual_context: strip_segment_header(&event.title),
                prev_chunk_summary_hash: prev_chunk_summary_hash.clone(),
                is_redundant: false,
                needs_review: !review_reasons.is_empty(),
                review_reasons,
            });
        }
        self.chunk_summaries
            .push((chunk_sequence, chunk_summary.trim().to_string()));
    }

    pub fn build(mut self, version: u32) -> VideoCapsule {
        for evidence in &mut self.evidences {
            evidence.version = version;
        }
        self.chunk_summaries.sort_by_key(|(sequence, _)| *sequence);
        self.chunk_summaries.dedup_by_key(|(sequence, _)| *sequence);

        // Build global summary from chunk summaries (without [Chunk N] prefix)
        let global_summary = self
            .chunk_summaries
            .iter()
            .filter(|(_, summary)| !summary.is_empty())
            .map(|(_, summary)| strip_segment_header(summary.trim()))
            .collect::<Vec<_>>()
            .join("\n\n");
        VideoCapsule {
            ir_schema_version: IR_SCHEMA_VERSION,
            capsule_id: format!("{}_{}", self.source_hash, version),
            source_hash: self.source_hash,
            source_title: self.source_title,
            version,
            total_duration: self.total_duration,
            processed_at: Utc::now().to_rfc3339(),
            model_used: self.model_used,
            evidences: self.evidences,
            global_summary,
            compilation_mode: CompileMode::CloudPrecision,
            warnings: Vec::new(),
            source_input: String::new(),
        }
    }
}

/// Remove segment-numbering lead-ins ("本片段", "此片段", ...) and
/// speaker prefixes ("讲师", "用户", "演示者") from the start of a
/// summary string so that merged summaries read as factual descriptions
/// without repetitive subject openings.
fn strip_segment_header(text: &str) -> String {
    let text = text.trim();
    let mut result = text.to_string();

    // Phase 1: strip known lead-in prefixes from the start
    const PREFIXES: &[&str] = &[
        "本片段作为",
        "本片段展示了",
        "本片段演示了",
        "本片段重点在于",
        "本片段聚焦于",
        "本片段继续",
        "本片段是",
        "本片段从",
        "本片段通过",
        "本片段首先",
        "本片段为",
        "本片段中",
        "本片段",
        "此片段是",
        "此片段",
        "该片段",
        "本段",
        // Speaker prefixes — strip so descriptions read factually
        "讲师",
        "用户",
        "演示者",
        "操作者",
    ];
    for prefix in PREFIXES {
        if let Some(rest) = result.strip_prefix(prefix) {
            let rest = rest.trim();
            if !rest.is_empty() {
                result = rest.to_string();
            }
            break;
        }
    }

    // Phase 2: replace mid-sentence speaker references that escaped
    // the prefix pass (e.g. "构建完成后，用户打开文件夹").
    const SUBJECT_PATTERNS: &[&str] = &[
        "用户在",
        "用户指出",
        "用户选择",
        "用户创建",
        "用户添加",
        "用户进入",
        "用户点击",
        "用户打开",
        "用户解释",
        "用户将",
        "用户说明",
        "用户展示",
        "用户调整",
        "用户设置",
    ];
    for pattern in SUBJECT_PATTERNS {
        result = result.replace(pattern, "");
    }

    // Clean up doubled whitespace/punctuation from replacements
    let result = result
        .replace("，，", "，")
        .replace("。。", "。")
        .replace("，。", "。")
        .replace("  ", " ");

    result.trim().to_string()
}

fn parse_evidence_type(value: &str) -> EvidenceType {
    match value.trim().to_ascii_lowercase().as_str() {
        "fact" => EvidenceType::Fact,
        "procedure" | "demonstration" => EvidenceType::Procedure,
        "failure" => EvidenceType::Failure,
        "verification" => EvidenceType::Verification,
        "draft" => EvidenceType::Draft,
        _ => EvidenceType::Concept,
    }
}

fn validate_capsule(capsule: &VideoCapsule) -> Result<(), String> {
    validate_source_hash(&capsule.source_hash)?;
    if capsule.version == 0 {
        return Err("capsule version must start at 1".to_string());
    }
    if capsule.evidences.is_empty() && capsule.warnings.is_empty() {
        return Err("empty capsule must include a warning".to_string());
    }
    Ok(())
}

fn validate_source_hash(hash: &str) -> Result<(), String> {
    if hash.len() != 64 || !hash.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err(format!("invalid source hash: {hash}"));
    }
    Ok(())
}

fn atomic_replace(path: &Path, data: &[u8]) -> Result<(), String> {
    let temp = path.with_extension(format!("tmp.{}", Uuid::new_v4()));
    write_new_file(&temp, data)?;
    fs::rename(&temp, path).map_err(|e| format!("atomic replace failed: {e}"))?;
    Ok(())
}

fn write_new_file(path: &Path, data: &[u8]) -> Result<(), String> {
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|e| format!("failed to create {path:?}: {e}"))?;
    file.write_all(data)
        .map_err(|e| format!("failed to write {path:?}: {e}"))?;
    file.sync_all()
        .map_err(|e| format!("failed to sync {path:?}: {e}"))?;
    Ok(())
}

fn file_is_stale(path: &Path, max_age: Duration) -> bool {
    fs::metadata(path)
        .and_then(|meta| meta.modified())
        .map(|modified| {
            SystemTime::now()
                .duration_since(modified)
                .map(|age| age > max_age)
                .unwrap_or(true)
        })
        .unwrap_or(true)
}

/// Compute the SHA-256 hex digest of a file's content.
pub fn file_hash(path: &Path) -> Result<String, String> {
    let data = fs::read(path).map_err(|e| format!("failed to read {path:?}: {e}"))?;
    Ok(format!("{:x}", Sha256::digest(&data)))
}

fn parse_version_filename(path: &Path, _extension: &str) -> Option<u32> {
    let name = path.file_stem()?.to_str()?;
    if let Some(rest) = name.strip_prefix('v') {
        rest.parse::<u32>().ok()
    } else {
        None
    }
}

#[cfg(test)]
fn temp_store() -> (FileCapsuleStore, PathBuf) {
    let dir = std::env::temp_dir().join(format!("vna-test-{}", Uuid::new_v4()));
    let store = FileCapsuleStore::new(dir.clone());
    (store, dir)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builder_propagates_version_to_evidence() {
        let mut builder = CapsuleBuilder::new(
            "abc".to_string(),
            "Test source".to_string(),
            "model".to_string(),
            10.0,
        );
        builder.add_chunk(
            0,
            vec![crate::compile::RawEvent {
                title: "event".to_string(),
                event_frame_indexes: vec![1, 2],
                description: "description".to_string(),
                event_type: "fact".to_string(),
                speaker: None,
                confidence: 0.8,
            }],
            "summary",
            &HashMap::from([(1, 1.0), (2, 2.0)]),
            None,
        );
        let capsule = builder.build(7);
        assert_eq!(capsule.evidences[0].version, 7);
    }

    #[test]
    fn immutable_insert_and_replay() {
        let (mut store, dir) = temp_store();
        let source_hash = "a".repeat(64);
        let version = store.reserve_next_version(&source_hash).unwrap();
        let mut capsule = CapsuleBuilder::new(
            source_hash.clone(),
            "Test source".to_string(),
            "model".to_string(),
            10.0,
        )
        .build(version);
        capsule
            .warnings
            .push("test capsule has no evidence".to_string());
        store.insert(capsule).unwrap();
        assert_eq!(store.get(&source_hash, version).unwrap().version, version);
        assert_eq!(store.list_versions(&source_hash).unwrap().len(), 1);
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn legacy_local_draft_capsule_loads_as_cloud_precision() {
        // VN-LDRFT-001 removed CompileMode::LocalDraft but legacy capsules
        // written under the old variant must still deserialize (SPEC-IR-005
        // immutable compilation history). The serde alias on the enum
        // variant reinterprets "local_draft" as CloudPrecision on read.
        let legacy = serde_json::json!({
            "ir_schema_version": 2,
            "capsule_id": "legacy",
            "source_hash": "a".repeat(64),
            "source_title": "Legacy",
            "version": 1,
            "total_duration": 10.0,
            "processed_at": "2025-01-01T00:00:00Z",
            "model_used": "model",
            "evidences": [],
            "global_summary": "",
            "compilation_mode": "local_draft",
            "warnings": [],
            "source_input": "",
        });
        let capsule: VideoCapsule = serde_json::from_value(legacy)
            .expect("legacy local_draft capsule must still deserialize");
        assert_eq!(capsule.compilation_mode, CompileMode::CloudPrecision);
    }
}
