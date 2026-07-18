//! Immutable, digest-verified v0.2 ExchangeBundle storage.
#![cfg_attr(not(test), allow(dead_code))]

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::thread;
use std::time::{Duration, SystemTime};

use chrono::Utc;
use sha2::{Digest, Sha256};
use uuid::Uuid;

use crate::compile_v3::ir::ExchangeBundle;
use crate::compile_v3::validate::{validate_value, write_bundle};

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StoredBundle {
    pub version: u32,
    pub bundle_id: String,
    pub source_title: String,
    pub content_digest: String,
    pub created_at: String,
    pub status: String,
}

pub trait BundleStore: Send + Sync {
    fn insert(&mut self, bundle: &ExchangeBundle) -> Result<StoredBundle, String>;
    fn get(&self, source_hash: &str, version: u32) -> Result<ExchangeBundle, String>;
    fn list_versions(&self, source_hash: &str) -> Result<Vec<StoredBundle>, String>;
}

pub struct FileBundleStore {
    base_dir: PathBuf,
}

impl FileBundleStore {
    pub fn new(base_dir: PathBuf) -> Self {
        Self { base_dir }
    }

    fn source_dir(&self, source_hash: &str) -> Result<PathBuf, String> {
        validate_hash(source_hash)?;
        Ok(self.base_dir.join(source_hash))
    }

    fn bundle_path(source_dir: &Path, version: u32) -> PathBuf {
        source_dir.join(format!("v{version}.bundle.json"))
    }

    fn digest_path(source_dir: &Path, version: u32) -> PathBuf {
        source_dir.join(format!("v{version}.bundle.sha256"))
    }

    fn versions_path(source_dir: &Path) -> PathBuf {
        source_dir.join("versions.json")
    }

    fn load_versions(source_dir: &Path, allow_missing: bool) -> Result<Vec<StoredBundle>, String> {
        let path = Self::versions_path(source_dir);
        if allow_missing && !path.exists() {
            return Ok(Vec::new());
        }
        let text = fs::read_to_string(&path)
            .map_err(|error| format!("required bundle index is unavailable: {error}"))?;
        let mut versions = serde_json::from_str::<Vec<StoredBundle>>(&text)
            .map_err(|error| format!("bundle index is corrupt: {error}"))?;
        versions.sort_by_key(|version| version.version);
        if versions
            .windows(2)
            .any(|pair| pair[0].version == pair[1].version)
        {
            return Err("bundle index contains duplicate versions".to_string());
        }
        Ok(versions)
    }

    fn save_versions(source_dir: &Path, versions: &[StoredBundle]) -> Result<(), String> {
        let bytes = serde_json::to_vec_pretty(versions)
            .map_err(|error| format!("bundle index serialization failed: {error}"))?;
        atomic_replace(&Self::versions_path(source_dir), &bytes)
    }

    fn acquire_source_lock(&self, source_hash: &str) -> Result<SourceLock, String> {
        let source_dir = self.source_dir(source_hash)?;
        fs::create_dir_all(&source_dir)
            .map_err(|error| format!("failed to create bundle directory: {error}"))?;
        let path = source_dir.join(".write.lock");
        for _ in 0..250 {
            match OpenOptions::new().write(true).create_new(true).open(&path) {
                Ok(mut file) => {
                    let _ = writeln!(file, "{} {}", std::process::id(), Utc::now());
                    let _ = file.sync_all();
                    return Ok(SourceLock { path });
                }
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                    if file_is_stale(&path, Duration::from_secs(300)) {
                        let _ = fs::remove_file(&path);
                        continue;
                    }
                    thread::sleep(Duration::from_millis(20));
                }
                Err(error) => return Err(format!("failed to acquire bundle lock: {error}")),
            }
        }
        Err(format!(
            "timed out acquiring bundle storage lock for {source_hash}"
        ))
    }

    pub fn has_versions(&self, source_hash: &str) -> bool {
        self.source_dir(source_hash)
            .and_then(|dir| Self::load_versions(&dir, false))
            .is_ok_and(|versions| !versions.is_empty())
    }
}

impl BundleStore for FileBundleStore {
    fn insert(&mut self, bundle: &ExchangeBundle) -> Result<StoredBundle, String> {
        validate_bundle_for_local_storage(bundle)?;
        let source_hash = bundle_source_hash(bundle)?;
        let source_dir = self.source_dir(&source_hash)?;
        fs::create_dir_all(&source_dir)
            .map_err(|error| format!("failed to create bundle directory: {error}"))?;
        let _lock = self.acquire_source_lock(&source_hash)?;
        let mut versions = Self::load_versions(&source_dir, true)?;
        let version = versions
            .last()
            .map(|stored| stored.version)
            .unwrap_or(0)
            .checked_add(1)
            .ok_or_else(|| "bundle version overflow".to_string())?;
        let bundle_path = Self::bundle_path(&source_dir, version);
        let digest_path = Self::digest_path(&source_dir, version);
        if bundle_path.exists() || digest_path.exists() {
            return Err(format!(
                "bundle {source_hash} v{version} already exists; versions are immutable"
            ));
        }

        let bytes =
            write_bundle(bundle).map_err(|error| format!("bundle write failed: {error}"))?;
        let file_digest = format!("sha256:{:x}", Sha256::digest(&bytes));
        commit_new_file(&bundle_path, &bytes)?;
        if let Err(error) = commit_new_file(&digest_path, file_digest.as_bytes()) {
            return Err(format!(
                "bundle bytes were committed but mandatory digest metadata failed: {error}"
            ));
        }

        let stored = StoredBundle {
            version,
            bundle_id: bundle.exchange_manifest.bundle_id.clone(),
            source_title: bundle
                .sources
                .first()
                .map(|source| source.source.title.clone())
                .unwrap_or_default(),
            content_digest: file_digest,
            created_at: bundle.compilation.created_at.clone(),
            status: bundle.capsule.status.clone(),
        };
        versions.push(stored.clone());
        Self::save_versions(&source_dir, &versions)?;
        Ok(stored)
    }

    fn get(&self, source_hash: &str, version: u32) -> Result<ExchangeBundle, String> {
        let source_dir = self.source_dir(source_hash)?;
        let versions = Self::load_versions(&source_dir, false)?;
        let indexed = versions
            .iter()
            .find(|stored| stored.version == version)
            .ok_or_else(|| {
                format!("bundle version {version} is not present in the mandatory index")
            })?;
        let digest_path = Self::digest_path(&source_dir, version);
        let sidecar_digest = fs::read_to_string(&digest_path)
            .map_err(|error| format!("mandatory bundle digest is unavailable: {error}"))?;
        let sidecar_digest = sidecar_digest.trim();
        if sidecar_digest != indexed.content_digest {
            return Err(format!(
                "bundle {version} digest metadata mismatch: index={}, sidecar={sidecar_digest}",
                indexed.content_digest
            ));
        }

        let bytes = fs::read(Self::bundle_path(&source_dir, version))
            .map_err(|error| format!("read bundle {version}: {error}"))?;
        let actual_digest = format!("sha256:{:x}", Sha256::digest(&bytes));
        if actual_digest != indexed.content_digest {
            return Err(format!(
                "bundle {version} digest mismatch: expected {}, got {actual_digest}",
                indexed.content_digest
            ));
        }
        let text = String::from_utf8(bytes)
            .map_err(|_| format!("stored bundle {version} is not valid UTF-8"))?;
        let bundle = serde_json::from_str::<ExchangeBundle>(&text)
            .map_err(|error| format!("stored bundle {version} deserialize failed: {error}"))?;
        validate_bundle_for_local_storage(&bundle)?;
        if bundle_source_hash(&bundle)? != source_hash {
            return Err("stored bundle source digest does not match its directory".to_string());
        }
        Ok(bundle)
    }

    fn list_versions(&self, source_hash: &str) -> Result<Vec<StoredBundle>, String> {
        let source_dir = self.source_dir(source_hash)?;
        Self::load_versions(&source_dir, false)
    }
}

fn validate_hash(hash: &str) -> Result<(), String> {
    if hash.len() != 64
        || !hash
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
    {
        return Err(format!("invalid source hash: {hash}"));
    }
    Ok(())
}

fn bundle_source_hash(bundle: &ExchangeBundle) -> Result<String, String> {
    let revision_id = bundle
        .compilation
        .source_revision_ids
        .first()
        .ok_or_else(|| "bundle compilation has no source revision".to_string())?;
    let revision = bundle
        .sources
        .iter()
        .find(|entry| entry.revision.source_revision_id == *revision_id)
        .ok_or_else(|| "bundle source revision does not resolve".to_string())?;
    let hash = revision
        .revision
        .content_digest
        .strip_prefix("sha256:")
        .ok_or_else(|| "bundle source digest must use sha256".to_string())?
        .to_string();
    validate_hash(&hash)?;
    Ok(hash)
}

fn validate_bundle_for_local_storage(bundle: &ExchangeBundle) -> Result<(), String> {
    let value = serde_json::to_value(bundle).map_err(|error| error.to_string())?;
    let report = validate_value(&value);
    let blocking = report
        .issues
        .iter()
        .filter(|issue| !issue.code.starts_with("VN_SIGNATURE"))
        .map(|issue| format!("{} at {}: {}", issue.code, issue.path, issue.message))
        .collect::<Vec<_>>();
    if blocking.is_empty() {
        Ok(())
    } else {
        Err(format!(
            "bundle failed structural validation: {}",
            blocking.join("; ")
        ))
    }
}

fn commit_new_file(path: &Path, bytes: &[u8]) -> Result<(), String> {
    let temp = path.with_extension(format!("tmp-{}", Uuid::new_v4()));
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temp)
        .map_err(|error| format!("failed to create temporary bundle file: {error}"))?;
    let result = (|| {
        file.write_all(bytes)
            .map_err(|error| format!("failed to write bundle file: {error}"))?;
        file.sync_all()
            .map_err(|error| format!("failed to sync bundle file: {error}"))?;
        fs::hard_link(&temp, path).map_err(|error| {
            if error.kind() == std::io::ErrorKind::AlreadyExists {
                format!("immutable bundle file already exists: {}", path.display())
            } else {
                format!("failed to commit bundle file: {error}")
            }
        })?;
        Ok(())
    })();
    let _ = fs::remove_file(&temp);
    result
}

fn atomic_replace(path: &Path, bytes: &[u8]) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "bundle index has no parent directory".to_string())?;
    fs::create_dir_all(parent).map_err(|error| format!("create bundle index dir: {error}"))?;
    let temp = path.with_extension(format!("tmp-{}", Uuid::new_v4()));
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temp)
        .map_err(|error| format!("create bundle index temp: {error}"))?;
    file.write_all(bytes)
        .map_err(|error| format!("write bundle index temp: {error}"))?;
    file.sync_all()
        .map_err(|error| format!("sync bundle index temp: {error}"))?;
    if path.exists() {
        fs::remove_file(path).map_err(|error| format!("replace bundle index: {error}"))?;
    }
    fs::rename(&temp, path).map_err(|error| format!("commit bundle index: {error}"))
}

fn file_is_stale(path: &Path, max_age: Duration) -> bool {
    fs::metadata(path)
        .and_then(|metadata| metadata.modified())
        .ok()
        .and_then(|modified| SystemTime::now().duration_since(modified).ok())
        .is_some_and(|age| age > max_age)
}

struct SourceLock {
    path: PathBuf,
}

impl Drop for SourceLock {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.path);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::compile::{CompileMode, Evidence, EvidenceType, VideoCapsule};
    use crate::compile_v3::convert;

    const SOURCE_HASH: &str = "aabbccddee00112233445566778899aabbccddee00112233445566778899aabb";

    fn dummy_bundle(seed: &str) -> ExchangeBundle {
        convert(&VideoCapsule {
            ir_schema_version: 2,
            capsule_id: format!("cap-{seed}"),
            source_hash: SOURCE_HASH.to_string(),
            source_title: "Test Source".to_string(),
            version: 1,
            total_duration: 10.0,
            processed_at: "2026-07-15T00:00:00Z".to_string(),
            model_used: "test-model".to_string(),
            evidences: vec![Evidence {
                id: format!("evidence-{seed}"),
                source_hash: SOURCE_HASH.to_string(),
                version: 1,
                chunk_sequence: 0,
                content: "Legacy interpretation".to_string(),
                timestamp_start_sec: 0.0,
                timestamp_end_sec: 2.0,
                evidence_type: EvidenceType::Concept,
                speaker: None,
                confidence: 0.5,
                visual_context: String::new(),
                prev_chunk_summary_hash: None,
                is_redundant: false,
                needs_review: true,
                review_reasons: vec![],
            }],
            global_summary: String::new(),
            compilation_mode: CompileMode::CloudPrecision,
            warnings: vec![],
            source_input: String::new(),
        })
        .expect("dummy conversion")
    }

    #[test]
    fn file_store_insert_list_and_get_round_trip() {
        let dir = tempfile::tempdir().expect("temp dir");
        let mut store = FileBundleStore::new(dir.path().to_path_buf());
        let bundle = dummy_bundle("one");

        let stored = store.insert(&bundle).expect("insert should succeed");
        assert_eq!(stored.version, 1);
        let versions = store.list_versions(SOURCE_HASH).expect("list versions");
        assert_eq!(versions.len(), 1);
        let loaded = store.get(SOURCE_HASH, 1).expect("get");
        assert_eq!(
            loaded.exchange_manifest.bundle_id,
            bundle.exchange_manifest.bundle_id
        );
    }

    #[test]
    fn concurrent_inserts_allocate_distinct_immutable_versions() {
        let dir = tempfile::tempdir().expect("temp dir");
        let root = dir.path().to_path_buf();
        let handles = (0..8)
            .map(|index| {
                let root = root.clone();
                thread::spawn(move || {
                    let mut store = FileBundleStore::new(root);
                    store
                        .insert(&dummy_bundle(&index.to_string()))
                        .unwrap()
                        .version
                })
            })
            .collect::<Vec<_>>();
        let mut versions = handles
            .into_iter()
            .map(|handle| handle.join().expect("insert thread"))
            .collect::<Vec<_>>();
        versions.sort_unstable();
        assert_eq!(versions, (1..=8).collect::<Vec<_>>());
    }

    #[test]
    fn file_store_rejects_tampering_and_missing_metadata() {
        let dir = tempfile::tempdir().expect("temp dir");
        let mut store = FileBundleStore::new(dir.path().to_path_buf());
        store.insert(&dummy_bundle("tamper")).expect("insert");
        let source_dir = dir.path().join(SOURCE_HASH);
        fs::write(source_dir.join("v1.bundle.json"), b"{}").unwrap();
        assert!(store.get(SOURCE_HASH, 1).is_err());

        fs::remove_file(source_dir.join("versions.json")).unwrap();
        assert!(store.get(SOURCE_HASH, 1).is_err());
    }

    #[test]
    fn file_store_rejects_structurally_invalid_bundle() {
        let dir = tempfile::tempdir().expect("temp dir");
        let mut store = FileBundleStore::new(dir.path().to_path_buf());
        let mut bundle = dummy_bundle("invalid");
        bundle.compilation.execution_plan.plan_digest = "sha256:invalid".to_string();
        assert!(store.insert(&bundle).is_err());
    }

    #[test]
    fn file_store_rejects_invalid_hash_and_missing_version() {
        let dir = tempfile::tempdir().expect("temp dir");
        let store = FileBundleStore::new(dir.path().to_path_buf());
        assert!(store.list_versions("").is_err());
        assert!(store.get(SOURCE_HASH, 999).is_err());
    }
}
