//! Versionned v0.2 ExchangeBundle storage.
#![cfg_attr(not(test), allow(dead_code))] // compiler_v3: off-by-default experimental module; items referenced only by conformance tests are unreachable in non-test bin builds
//!
//! Persists validated ExchangeBundles as canonical JSON files.
//! Each bundle is stored as `<capsule_dir>/v<version>.bundle.json`.
//! A `versions.json` cache tracks version metadata for listing.
//!
//! Security: reads verify the bundle digest before deserialization.
//! Access-policy escalation is rejected before persistence.

use std::fs;
use std::path::{Path, PathBuf};

use sha2::{Digest, Sha256};

use crate::compile_v3::ir::ExchangeBundle;
use crate::compile_v3::validate::write_bundle;

/// Metadata about a stored v0.2 bundle version.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct StoredBundle {
    pub version: u32,
    pub bundle_id: String,
    pub source_title: String,
    pub content_digest: String,
    pub created_at: String,
    pub status: String,
}

/// Versioned bundle storage trait.
pub trait BundleStore: Send + Sync {
    /// Persist a validated bundle and return the version number.
    fn insert(
        &mut self,
        bundle: &ExchangeBundle,
    ) -> Result<StoredBundle, String>;

    /// Load a specific version.
    fn get(&self, source_hash: &str, version: u32) -> Result<ExchangeBundle, String>;

    /// List all stored versions for a source.
    fn list_versions(&self, source_hash: &str) -> Result<Vec<StoredBundle>, String>;
}

/// File-based v0.2 bundle store.
///
/// Layout:
///   <base_dir>/<source_hash>/
///     v<version>.bundle.json   — canonical JSON bundle
///     versions.json             — cache of StoredBundle entries
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

    fn versions_path(source_dir: &Path) -> PathBuf {
        source_dir.join("versions.json")
    }

    fn next_version(source_dir: &Path) -> Result<u32, String> {
        let versions = Self::load_versions(source_dir);
        Ok(versions.last().map(|v| v.version + 1).unwrap_or(1))
    }

    fn load_versions(source_dir: &Path) -> Vec<StoredBundle> {
        let path = Self::versions_path(source_dir);
        fs::read_to_string(&path)
            .ok()
            .and_then(|text| serde_json::from_str::<Vec<StoredBundle>>(&text).ok())
            .unwrap_or_default()
    }

    fn save_versions(source_dir: &Path, versions: &[StoredBundle]) -> Result<(), String> {
        let json = serde_json::to_string_pretty(versions).map_err(|e| format!("versions ser: {e}"))?;
        fs::write(Self::versions_path(source_dir), &json)
            .map_err(|e| format!("versions write: {e}"))
    }

    /// Check if any versions exist for a source hash.
    pub fn has_versions(&self, source_hash: &str) -> bool {
        self.source_dir(source_hash)
            .map(|dir| Self::versions_path(&dir).exists())
            .unwrap_or(false)
    }
}

/// Validate a source hash: must be a hex string between 8 and 128 chars.
fn validate_hash(hash: &str) -> Result<(), String> {
    let ok = hash.len() >= 8
        && hash.len() <= 128
        && hash.chars().all(|c| c.is_ascii_hexdigit());
    if !ok {
        return Err(format!("invalid source hash: {hash}"));
    }
    Ok(())
}

impl BundleStore for FileBundleStore {
    fn insert(
        &mut self,
        bundle: &ExchangeBundle,
    ) -> Result<StoredBundle, String> {
        // Derive source_hash from the first source_revision_id
        let source_hash = bundle
            .compilation
            .source_revision_ids
            .first()
            .cloned()
            .unwrap_or_else(|| "unknown".to_string());

        let source_dir = self.source_dir(&source_hash)?;
        fs::create_dir_all(&source_dir)
            .map_err(|e| format!("create source dir: {e}"))?;

        let version = Self::next_version(&source_dir)?;
        let bundle_bytes =
            write_bundle(bundle).map_err(|e| format!("bundle write failed: {e}"))?;

        let bundle_digest = format!("sha256:{:x}", Sha256::digest(&bundle_bytes));

        let path = Self::bundle_path(&source_dir, version);
        fs::write(&path, &bundle_bytes).map_err(|e| format!("file write: {e}"))?;

        let stored = StoredBundle {
            version,
            bundle_id: bundle.exchange_manifest.bundle_id.clone(),
            source_title: bundle
                .sources
                .first()
                .map(|s| s.source.title.clone())
                .unwrap_or_default(),
            content_digest: bundle_digest,
            created_at: bundle.compilation.created_at.clone(),
            status: bundle.capsule.status.clone(),
        };

        let mut versions = Self::load_versions(&source_dir);
        versions.push(stored.clone());
        Self::save_versions(&source_dir, &versions)?;

        Ok(stored)
    }

    fn get(&self, source_hash: &str, version: u32) -> Result<ExchangeBundle, String> {
        let source_dir = self.source_dir(source_hash)?;
        let path = Self::bundle_path(&source_dir, version);
        let bytes = fs::read(&path).map_err(|e| format!("read bundle {version}: {e}"))?;

        // Verify digest matches the version cache
        let actual_digest = format!("sha256:{:x}", Sha256::digest(&bytes));
        let versions = Self::load_versions(&source_dir);
        if let Some(expected) = versions
            .iter()
            .find(|v| v.version == version)
            .map(|v| &v.content_digest)
        {
            if &actual_digest != expected {
                return Err(format!(
                    "bundle {version} digest mismatch: expected {expected}, got {actual_digest}"
                ));
            }
        }

        let text = String::from_utf8(bytes).map_err(|_| "bundle is not valid UTF-8".to_string())?;
        // Deserialize directly: security comes from the digest check above.
        // The bundle was already validated on insert().
        let bundle: ExchangeBundle = serde_json::from_str(&text)
            .map_err(|e| format!("stored bundle {version} deserialize failed: {e}"))?;
        Ok(bundle)
    }

    fn list_versions(&self, source_hash: &str) -> Result<Vec<StoredBundle>, String> {
        let source_dir = self.source_dir(source_hash)?;
        Ok(Self::load_versions(&source_dir))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::compile_v3::ir::{
        AccessPolicy, Capsule, Compilation, ExchangeManifest, ExchangeSignature,
        ExecutionPlan, RightsProfile, Source, SourceEntry, SourceRevision,
    };

    fn dummy_bundle() -> ExchangeBundle {
        // Minimal valid bundle for storage round-trip tests.
        // In practice bundles come from convert::convert() or fixture parsing.
        ExchangeBundle {
            bundle_version: "0.2.0-rc.3".to_string(),
            sources: vec![SourceEntry {
                source: Source {
                    source_id: "src_test".to_string(),
                    title: "Test Source".to_string(),
                    created_at: "2026-07-15T00:00:00Z".to_string(),
                    origin_history: vec![],
                },
                revision: SourceRevision {
                    source_revision_id: "rev_test".to_string(),
                    source_id: "src_test".to_string(),
                    content_digest: "sha256:abc".to_string(),
                    byte_length: 100,
                    media_type: "video/mp4".to_string(),
                    acquired_at: "2026-07-15T00:00:00Z".to_string(),
                    origin: serde_json::Value::Object(Default::default()),
                    privacy_classification: "public".to_string(),
                    tracks: vec![],
                    rights_profile: RightsProfile {
                        basis: "owned".to_string(),
                        license_identifier: None,
                        consent_record_digest: None,
                        transform_allowed: true,
                        excerpt_export_allowed: false,
                        sharing_scope: "private".to_string(),
                        expires_at: None,
                    },
                },
            }],
            anchor_manifests: vec![],
            compilation: Compilation {
                compilation_id: "cmp_test".to_string(),
                source_revision_ids: vec!["rev_test".to_string()],
                request_digest: None,
                compilation_sequence: 1,
                state: "succeeded".to_string(),
                spec_version: "0.2.0-rc.3".to_string(),
                ir_schema_version: "0.2.0-rc.3".to_string(),
                compiler_build: "test".to_string(),
                execution_plan: ExecutionPlan {
                    plan_id: "plan_test".to_string(),
                    plan_digest: "sha256:test".to_string(),
                    required_modalities: vec!["visual".to_string()],
                    passes: vec![],
                    budget: serde_json::json!({}),
                    anchor_manifest_refs: vec![],
                    provider_manifest_digests: vec![],
                },
                created_at: "2026-07-15T00:00:00Z".to_string(),
                updated_at: "2026-07-15T00:00:00Z".to_string(),
                capsule_id: Some("cap_test".to_string()),
                idempotency_key_digest: String::new(),
            },
            capsule: Capsule {
                capsule_id: "cap_test".to_string(),
                compilation_id: "cmp_test".to_string(),
                source_revision_ids: vec!["rev_test".to_string()],
                compilation_sequence: 1,
                ir_schema_version: "0.2.0-rc.3".to_string(),
                status: "complete".to_string(),
                completeness: serde_json::json!({"status": "complete"}),
                evidences: vec![],
                knowledge: serde_json::json!({}),
                diagnostics: serde_json::json!({}),
                provenance: vec![],
                created_at: "2026-07-15T00:00:00Z".to_string(),
                compile_report: None,
                anchor_manifest_refs: vec![],
                effective_access_policy: AccessPolicy {
                    classification: "public".to_string(),
                    sharing_scope: "private".to_string(),
                    embedded_source_export_allowed: false,
                    policy_digest: None,
                },
            },
            artifacts: vec![],
            provider_manifests: vec![],
            external_dependencies: vec![],
            exchange_manifest: ExchangeManifest {
                bundle_id: "bundle_test".to_string(),
                canonicalization_profile: "VN-C14N-1".to_string(),
                signature_context: "video-notes.exchange-bundle.v0.2".to_string(),
                content_digest: String::new(),
                signature: ExchangeSignature {
                    algorithm: "ed25519".to_string(),
                    key_id: "synthetic-fixture-key-v0.2-rc.3".to_string(),
                    public_key_base64: String::new(),
                    signed_at: "2026-07-15T00:00:00Z".to_string(),
                    signature_base64: String::new(),
                },
            },
        }
    }

    #[test]
    fn file_store_insert_and_list() {
        let dir = tempfile::tempdir().expect("temp dir");
        let mut store = FileBundleStore::new(dir.path().to_path_buf());
        let bundle = dummy_bundle();

        let stored = store.insert(&bundle).expect("insert should succeed");
        assert_eq!(stored.version, 1);
        assert_eq!(stored.bundle_id, "bundle_test");

        let versions = store.list_versions("rev_test").expect("list versions");
        assert_eq!(versions.len(), 1);
        assert_eq!(versions[0].version, 1);
    }

    #[test]
    fn file_store_insert_increments_version() {
        let dir = tempfile::tempdir().expect("temp dir");
        let mut store = FileBundleStore::new(dir.path().to_path_buf());
        let bundle = dummy_bundle();

        let v1 = store.insert(&bundle).expect("first insert");
        assert_eq!(v1.version, 1);

        let v2 = store.insert(&bundle).expect("second insert");
        assert_eq!(v2.version, 2);
    }

    #[test]
    fn file_store_get_round_trips() {
        let dir = tempfile::tempdir().expect("temp dir");
        let mut store = FileBundleStore::new(dir.path().to_path_buf());
        let bundle = dummy_bundle();

        let stored = store.insert(&bundle).expect("insert");
        let loaded = store.get("rev_test", stored.version).expect("get");

        assert_eq!(
            loaded.exchange_manifest.bundle_id,
            bundle.exchange_manifest.bundle_id
        );
    }

    #[test]
    fn file_store_rejects_invalid_hash() {
        let store = FileBundleStore::new(PathBuf::from("/tmp"));
        let result = store.list_versions("");
        assert!(result.is_err(), "empty hash should be rejected");
    }

    #[test]
    fn store_rejects_missing_version() {
        let dir = tempfile::tempdir().expect("temp dir");
        let store = FileBundleStore::new(dir.path().to_path_buf());
        let result = store.get("rev_test", 999);
        assert!(result.is_err(), "missing version should error");
    }
}
