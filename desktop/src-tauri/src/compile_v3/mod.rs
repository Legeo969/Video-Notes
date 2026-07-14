//! Experimental learning-material compiler contracts.
//!
//! This module is disabled by default and exists behind the `compiler_v3` Cargo
//! feature. It validates and round-trips Spec v0.2 exchange bundles without changing
//! the legacy v2.1 Capsule reader or writer.
#![cfg_attr(not(test), allow(unused_imports))] // compiler_v3: off-by-default experimental module; re-exports are unreachable in non-test bin builds

pub mod canonical;
pub mod convert;
pub mod ir;
pub mod storage;
pub mod trust;
pub mod validate;

pub use convert::convert;
pub use ir::{ExchangeBundle, SPEC_VERSION};
pub use storage::{BundleStore, FileBundleStore, StoredBundle};
pub use trust::{KeyStatus, TrustError, TrustPolicy, TrustedSigningKey};
pub use validate::{
    parse_and_validate, parse_and_validate_with_policy, validate_value, validate_value_with_policy,
    write_bundle, ValidationIssue, ValidationReport,
};
