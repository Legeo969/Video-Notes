import sys
import os
import argparse

from src.app.cli.registry import CliCommand


class CheckOcrCommand:
    name = "check-ocr"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        pass

    def matches(self, args: argparse.Namespace) -> bool:
        return getattr(args, "check_ocr", False)

    def run(self, args: argparse.Namespace) -> int:
        try:
            import paddle
            import paddleocr
            import paddlex
        except Exception as exc:
            print(f"PaddleOCR 运行时不可用: {exc}", file=sys.stderr)
            return 1
        print(f"PaddleOCR: {getattr(paddleocr, '__version__', 'unknown')}")
        print(f"PaddlePaddle: {getattr(paddle, '__version__', 'unknown')}")
        print(f"PaddleX: {getattr(paddlex, '__version__', 'unknown')}")

        # PaddleX validates optional extras using distribution metadata. In a
        # PyInstaller build, imports may exist while the matching *.dist-info
        # directories are missing. Report the exact missing OCR-core metadata
        # before pipeline creation so packaging failures are actionable.
        try:
            from paddlex.utils import deps as paddlex_deps

            missing_ocr_core = [
                dep
                for dep in paddlex_deps.EXTRAS.get("ocr-core", {})
                if not paddlex_deps.is_dep_available(dep)
            ]
            if missing_ocr_core:
                print(
                    "PaddleX OCR-core 依赖元数据缺失: "
                    + ", ".join(missing_ocr_core),
                    file=sys.stderr,
                )
                return 1
            print("PaddleX OCR-core dependencies: OK")
        except Exception as exc:
            print(f"PaddleX OCR-core 依赖检查失败: {exc}", file=sys.stderr)
            return 1

        try:
            from src.infrastructure.video.ocr_engine import OCREngine

            engine = OCREngine(raise_on_error=True)
            runtime = engine._get_ocr()
            if runtime is None:
                reason = engine.disabled_reason() or "unknown initialization failure"
                print(f"PaddleOCR pipeline 初始化失败: {reason}", file=sys.stderr)
                return 1

            # Model creation alone does not load every cuDNN companion DLL.
            # Run one real inference so packaged builds fail early on error 126.
            import tempfile
            from PIL import Image, ImageDraw

            with tempfile.TemporaryDirectory(prefix="vna-ocr-probe-") as tmp_dir:
                probe_path = os.path.join(tmp_dir, "ocr_probe.png")
                image = Image.new("RGB", (640, 160), "white")
                ImageDraw.Draw(image).text((24, 52), "VIDEO NOTES OCR 123", fill="black")
                image.save(probe_path)
                engine.ocr_frame(probe_path)
        except Exception as exc:
            print(f"PaddleOCR pipeline 初始化失败: {exc}", file=sys.stderr)
            import traceback
            traceback.print_exc()
            return 1
        print(f"PaddleOCR inference probe: OK ({engine._device or 'unknown device'})")
        print(f"PaddleOCR pipeline: OK ({engine._device or 'unknown device'})")
        return 0


class DoctorCommand:
    name = "doctor"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        pass

    def matches(self, args: argparse.Namespace) -> bool:
        return getattr(args, "doctor", False)

    def run(self, args: argparse.Namespace) -> int:
        from src.application.diagnostics import run_diagnostics
        report = run_diagnostics()
        print(report.to_text())
        if report.has_errors:
            print("\n⚠️  有错误，请先解决后再使用本工具。", file=sys.stderr)
            return 1
        return 0


class IssueBundleCommand:
    name = "issue-bundle"

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        pass

    def matches(self, args: argparse.Namespace) -> bool:
        return getattr(args, "issue_bundle", False)

    def run(self, args: argparse.Namespace) -> int:
        from src.application.diagnostics import generate_issue_bundle
        output_dir = getattr(args, "output", "./output")
        print("🔍 正在收集诊断信息...")
        try:
            bundle_path = generate_issue_bundle(output_dir=output_dir)
            print(f"✅ 问题报告包已生成：{bundle_path}")
            print(f"   包含以下文件：")
            import zipfile
            with zipfile.ZipFile(bundle_path, "r") as zf:
                for name in zf.namelist():
                    info = zf.getinfo(name)
                    print(f"     - {name} ({info.file_size} bytes)")
            print()
            print("   提交 issue 时请附上此文件。")
        except Exception as exc:
            print(f"❌ 生成失败：{exc}", file=sys.stderr)
            return 1
        return 0


def _cmd_doctor() -> None:
    from src.application.diagnostics import run_diagnostics
    report = run_diagnostics()
    print(report.to_text())
    if report.has_errors:
        print("\n⚠️  有错误，请先解决后再使用本工具。", file=sys.stderr)


def _cmd_issue_bundle(output_dir: str) -> None:
    from src.application.diagnostics import generate_issue_bundle
    print("🔍 正在收集诊断信息...")
    try:
        bundle_path = generate_issue_bundle(output_dir=output_dir)
        print(f"✅ 问题报告包已生成：{bundle_path}")
        print(f"   包含以下文件：")
        import zipfile
        with zipfile.ZipFile(bundle_path, "r") as zf:
            for name in zf.namelist():
                info = zf.getinfo(name)
                print(f"     - {name} ({info.file_size} bytes)")
        print()
        print("   提交 issue 时请附上此文件。")
    except Exception as exc:
        print(f"❌ 生成失败：{exc}", file=sys.stderr)


def register_diagnostics(registry):
    registry.register(DoctorCommand())
    registry.register(IssueBundleCommand())
    registry.register(CheckOcrCommand())
