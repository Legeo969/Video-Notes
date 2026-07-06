from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GATE0 = ROOT / ".slim" / "deepwork" / "gate0"


def test_gate0_required_artifacts_exist_and_are_current() -> None:
    required = [
        "module_inventory.json",
        "import_graph.json",
        "test_baseline.json",
        "ui_feature_matrix.md",
        "rpc_contract.md",
        "sample_data_manifest.json",
    ]

    for name in required:
        path = GATE0 / name
        assert path.is_file(), name
        assert path.stat().st_size > 0, name

    baseline = json.loads((GATE0 / "test_baseline.json").read_text(encoding="utf-8"))
    assert baseline["commands"]["collect"]["returncode"] == 0
    assert baseline["commands"]["full"]["returncode"] == 0
    assert baseline["commands"]["full"]["summary"]["passed"] >= 640


def test_gate0_import_graph_records_no_production_legacy_gui_or_core_imports() -> None:
    graph = json.loads((GATE0 / "import_graph.json").read_text(encoding="utf-8"))
    findings = graph["architecture_findings"]

    assert findings["domain_upward_dependencies"] == []
    assert findings["forbidden_src_core_gui_or_pyside_imports"] == []
    assert findings["production_forbidden_src_core_gui_or_pyside_imports"] == []
    assert findings["application_direct_infrastructure_dependencies"] == []

    inventory = json.loads((GATE0 / "module_inventory.json").read_text(encoding="utf-8"))
    paths = {item["path"] for item in inventory}
    assert not any(path.startswith("src/gui/") for path in paths)
    assert not any(path.startswith("src/core/") for path in paths)
