from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
GATE0_DIR = ROOT / ".slim" / "deepwork" / "gate0"


@dataclass(frozen=True)
class CommandResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def run_command(command: list[str], timeout: int = 120) -> CommandResult:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
    )
    return CommandResult(
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def discover_files() -> list[Path]:
    ignored_parts = {
        ".git",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "node_modules",
        "dist",
        "target",
        ".venv",
        "venv",
    }
    result: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in ignored_parts for part in path.relative_to(ROOT).parts):
            continue
        result.append(path)
    return sorted(result)


def module_name(path: Path) -> str | None:
    if path.suffix != ".py":
        return None
    try:
        relative = path.relative_to(ROOT)
    except ValueError:
        return None
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def generate_module_inventory(files: list[Path], generated_at: str) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for path in files:
        if path.suffix not in {".py", ".rs", ".ts", ".js", ".svelte", ".json", ".toml", ".md", ".yaml", ".yml"}:
            continue
        text = read_text(path)
        item = {
            "path": rel(path),
            "kind": path.suffix.lstrip(".") or "file",
            "lines": 0 if not text else len(text.splitlines()),
            "bytes": path.stat().st_size,
            "generated_at": generated_at,
        }
        mod = module_name(path)
        if mod:
            item["module"] = mod
        inventory.append(item)
    return inventory


def parse_imports(path: Path) -> list[str]:
    if path.suffix != ".py":
        return []
    try:
        tree = ast.parse(read_text(path))
    except SyntaxError:
        return []
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add("." * node.level + node.module)
    return sorted(imports)


def generate_import_graph(files: list[Path], generated_at: str) -> dict[str, Any]:
    nodes = []
    edges = []
    domain_upward: list[dict[str, str]] = []
    application_infrastructure: list[dict[str, str]] = []
    forbidden_src_imports: list[dict[str, str]] = []
    production_forbidden_src_imports: list[dict[str, str]] = []
    forbidden_tokens = ("src.core", "src.gui", "PySide6")

    for path in files:
        mod = module_name(path)
        if not mod:
            continue
        imports = parse_imports(path)
        nodes.append({"module": mod, "path": rel(path)})
        for imported in imports:
            edges.append({"from": mod, "to": imported})
            if mod.startswith("src.domain") and imported.startswith(
                ("src.application", "src.infrastructure", "src.api", "src.gui")
            ):
                domain_upward.append({"from": mod, "to": imported})
            if mod.startswith("src.application") and imported.startswith("src.infrastructure"):
                application_infrastructure.append({"from": mod, "to": imported})
            if imported.startswith(forbidden_tokens) or imported == "PySide6":
                finding = {"from": mod, "to": imported}
                forbidden_src_imports.append(finding)
                if not mod.startswith("tests"):
                    production_forbidden_src_imports.append(finding)

    return {
        "generated_at": generated_at,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
        "architecture_findings": {
            "domain_upward_dependencies": domain_upward,
            "application_direct_infrastructure_dependencies": application_infrastructure,
            "forbidden_src_core_gui_or_pyside_imports": forbidden_src_imports,
            "production_forbidden_src_core_gui_or_pyside_imports": production_forbidden_src_imports,
        },
    }


def count_tests_by_file(files: list[Path]) -> list[dict[str, Any]]:
    result = []
    for path in files:
        relative = path.relative_to(ROOT)
        if not (relative.parts and relative.parts[0] == "tests" and path.name.startswith("test_") and path.suffix == ".py"):
            continue
        text = read_text(path)
        result.append({
            "path": rel(path),
            "test_functions": len(re.findall(r"^\s*def test_", text, flags=re.MULTILINE)),
            "test_classes": len(re.findall(r"^\s*class Test", text, flags=re.MULTILINE)),
            "skipped_file": "pytestmark = pytest.mark.skip" in text,
        })
    return result


def parse_pytest_summary(output: str) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    match = re.search(
        r"=+\s*(?P<body>[\d\s\w,]+?)\s+in\s+(?P<seconds>[\d.]+)s\s*=+",
        output,
    )
    if match:
        summary["raw"] = match.group("body").strip()
        summary["seconds"] = float(match.group("seconds"))
        for count, label in re.findall(r"(\d+)\s+([a-zA-Z]+)", match.group("body")):
            summary[label] = int(count)
    collected = re.search(r"collected\s+(\d+)\s+items", output)
    if collected:
        summary["collected"] = int(collected.group(1))
    return summary


def generate_test_baseline(files: list[Path], generated_at: str) -> dict[str, Any]:
    collect = run_command([sys.executable, "-m", "pytest", "--collect-only", "-q"], timeout=120)
    full = run_command([
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--ignore=tests/test_gate0_artifacts.py",
    ], timeout=180)
    test_files = count_tests_by_file(files)
    return {
        "generated_at": generated_at,
        "commands": {
            "collect": {
                "command": collect.command,
                "returncode": collect.returncode,
                "summary": parse_pytest_summary(collect.stdout + collect.stderr),
            },
            "full": {
                "command": full.command,
                "returncode": full.returncode,
                "summary": parse_pytest_summary(full.stdout + full.stderr),
                "stdout_tail": "\n".join((full.stdout or "").splitlines()[-30:]),
                "stderr_tail": "\n".join((full.stderr or "").splitlines()[-30:]),
            },
        },
        "test_files": test_files,
        "totals": {
            "test_files": len(test_files),
            "test_functions": sum(item["test_functions"] for item in test_files),
            "test_classes": sum(item["test_classes"] for item in test_files),
            "skipped_files": sum(1 for item in test_files if item["skipped_file"]),
        },
    }


def registered_rpc_methods() -> list[str]:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from src.api.server import create_dispatcher

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        dispatcher = create_dispatcher(output_dir=str(Path(tmp) / "output"))
    return sorted(dispatcher.__dict__["_handlers"])


def frontend_rpc_usage(files: list[Path]) -> dict[str, list[str]]:
    usage: dict[str, list[str]] = {}
    pattern = re.compile(r"engineCall(?:<[^>]+>)?\(\s*[\"']([^\"']+)[\"']")
    for path in files:
        if path.suffix not in {".svelte", ".ts"}:
            continue
        if not rel(path).startswith("desktop/src/"):
            continue
        methods = sorted(set(pattern.findall(read_text(path))))
        if methods:
            usage[rel(path)] = methods
    return usage


def generate_rpc_contract_md(files: list[Path], generated_at: str) -> str:
    backend = registered_rpc_methods()
    frontend = frontend_rpc_usage(files)
    used = sorted({method for methods in frontend.values() for method in methods})
    missing = sorted(set(used) - set(backend))
    extra = sorted(set(backend) - set(used))
    lines = [
        "# Gate 0 RPC Contract",
        "",
        f"Generated at: `{generated_at}`",
        "",
        "## Backend Registered Methods",
        "",
        *[f"- `{method}`" for method in backend],
        "",
        "## Frontend Engine Calls",
        "",
    ]
    for path, methods in sorted(frontend.items()):
        lines.append(f"### `{path}`")
        lines.extend(f"- `{method}`" for method in methods)
        lines.append("")
    lines.extend([
        "## Diff",
        "",
        f"- Frontend calls missing backend handlers: `{missing}`",
        f"- Backend handlers not currently called by frontend: `{extra}`",
        "",
    ])
    return "\n".join(lines)


def generate_ui_feature_matrix_md(files: list[Path], generated_at: str) -> str:
    page_files = sorted((ROOT / "desktop" / "src" / "pages").glob("*.svelte"))
    lines = [
        "# Gate 0 UI Feature Matrix",
        "",
        f"Generated at: `{generated_at}`",
        "",
        "| Page | Lines | RPC calls | Controls / signals |",
        "|---|---:|---|---|",
    ]
    call_pattern = re.compile(r"engineCall(?:<[^>]+>)?\(\s*[\"']([^\"']+)[\"']")
    control_pattern = re.compile(r"<(button|input|select|textarea|form)\b", re.IGNORECASE)
    for path in page_files:
        text = read_text(path)
        calls = sorted(set(call_pattern.findall(text)))
        controls = control_pattern.findall(text)
        lines.append(
            f"| `{path.name}` | {len(text.splitlines())} | "
            f"{', '.join(f'`{call}`' for call in calls) or '-'} | "
            f"{len(controls)} interactive tags |"
        )
    lines.extend([
        "",
        "## Source Files",
        "",
        *[f"- `{rel(path)}`" for path in page_files],
        "",
    ])
    return "\n".join(lines)


def generate_sample_data_manifest(files: list[Path], generated_at: str) -> dict[str, Any]:
    sample_roots = [
        ROOT / "runtime" / "manifests",
        ROOT / "templates",
        ROOT / "output",
        ROOT / "tests" / "fixtures",
    ]
    samples = []
    for root in sample_roots:
        if not root.exists():
            samples.append({"root": rel(root), "exists": False, "files": []})
            continue
        root_files = []
        for path in sorted(root.rglob("*")):
            if path.is_file():
                root_files.append({
                    "path": rel(path),
                    "bytes": path.stat().st_size,
                })
        samples.append({"root": rel(root), "exists": True, "files": root_files})

    return {
        "generated_at": generated_at,
        "samples": samples,
        "runtime_manifests": [
            json.loads(read_text(path))
            for path in sorted((ROOT / "runtime" / "manifests").glob("*.json"))
        ] if (ROOT / "runtime" / "manifests").is_dir() else [],
    }


def main() -> int:
    generated_at = datetime.now(timezone.utc).isoformat()
    GATE0_DIR.mkdir(parents=True, exist_ok=True)
    files = discover_files()

    write_json(GATE0_DIR / "module_inventory.json", generate_module_inventory(files, generated_at))
    write_json(GATE0_DIR / "import_graph.json", generate_import_graph(files, generated_at))
    write_json(GATE0_DIR / "test_baseline.json", generate_test_baseline(files, generated_at))
    (GATE0_DIR / "rpc_contract.md").write_text(
        generate_rpc_contract_md(files, generated_at),
        encoding="utf-8",
    )
    (GATE0_DIR / "ui_feature_matrix.md").write_text(
        generate_ui_feature_matrix_md(files, generated_at),
        encoding="utf-8",
    )
    write_json(GATE0_DIR / "sample_data_manifest.json", generate_sample_data_manifest(files, generated_at))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
