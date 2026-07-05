#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "[1/3] Python backend verification"
python -m pytest -q \
  --ignore=tests/test_collection_delete.py \
  --ignore=tests/test_smart_summary.py \
  --ignore=tests/test_provider_profile_settings.py

echo "[2/3] Svelte frontend verification"
pushd desktop >/dev/null
npm ci
npm run build
npx svelte-check --tsconfig ./tsconfig.json
popd >/dev/null

echo "[3/3] Rust/Tauri verification"
if command -v cargo >/dev/null 2>&1; then
  pushd desktop/src-tauri >/dev/null
  cargo fmt --check
  cargo check
  cargo test
  popd >/dev/null
elif [[ "${SKIP_RUST:-0}" == "1" ]]; then
  echo "[WARN] cargo not found; Rust/Tauri verification was explicitly skipped. This is not release-ready." >&2
else
  echo "[ERROR] cargo not found; install Rust with rustup before release verification." >&2
  echo "        Use SKIP_RUST=1 only for an intentional Python/frontend-only check." >&2
  exit 2
fi

echo "All enabled product verification gates passed."
