"""Diagnostics models — check results and summary report.

Reusable by both CLI (--doctor) and GUI (First-run Wizard).
"""

from dataclasses import dataclass, field
from typing import Literal


CheckStatus = Literal["ok", "warning", "error", "skipped"]


@dataclass
class DiagnosticCheck:
    """A single environment / configuration check result."""

    id: str                 # stable machine-readable id, e.g. "ffmpeg"
    name: str               # human-readable label, e.g. "FFmpeg"
    status: CheckStatus     # ok | warning | error | skipped
    message: str            # one-line summary
    suggestion: str | None = None   # fix suggestion for warning / error
    details: dict[str, str] = field(default_factory=dict)

    @property
    def is_ok(self) -> bool:
        return self.status == "ok"

    @property
    def is_error(self) -> bool:
        return self.status == "error"


@dataclass
class DiagnosticReport:
    """Aggregated results from EnvironmentChecker.run_all()."""

    checks: list[DiagnosticCheck]

    # ---- Derived properties ----

    @property
    def ok_count(self) -> int:
        return sum(1 for c in self.checks if c.status == "ok")

    @property
    def warning_count(self) -> int:
        return sum(1 for c in self.checks if c.status == "warning")

    @property
    def error_count(self) -> int:
        return sum(1 for c in self.checks if c.status == "error")

    @property
    def skipped_count(self) -> int:
        return sum(1 for c in self.checks if c.status == "skipped")

    @property
    def has_errors(self) -> bool:
        return self.error_count > 0

    @property
    def has_warnings(self) -> bool:
        return self.warning_count > 0

    @property
    def ready_for_basic_use(self) -> bool:
        """Basic use = ffmpeg + yt-dlp + writable output dir OK, no errors."""
        critical_ids = {"ffmpeg", "ffprobe", "ytdlp", "output_dir"}
        for c in self.checks:
            if c.id in critical_ids and c.is_error:
                return False
        return True

    @property
    def ready_for_advanced_use(self) -> bool:
        """Advanced use = basic OK + whisper + provider configured."""
        if not self.ready_for_basic_use:
            return False
        advanced_ids = {"whisper", "provider"}
        for c in self.checks:
            if c.id in advanced_ids and c.is_error:
                return False
        return True

    def get_check(self, check_id: str) -> DiagnosticCheck | None:
        for c in self.checks:
            if c.id == check_id:
                return c
        return None

    # ---- Text output (CLI --doctor) ----

    STATUS_ICON = {
        "ok": "✅",
        "warning": "⚠️",
        "error": "❌",
        "skipped": "⏭️",
    }

    def to_text(self) -> str:
        """Format the report as a human-readable text block for CLI output."""
        lines = ["Environment diagnostics", "=" * 60]
        for c in self.checks:
            icon = self.STATUS_ICON.get(c.status, "  ")
            line = f"  {icon} {c.name}: {c.message}"
            lines.append(line)
            if c.suggestion:
                lines.append(f"     → {c.suggestion}")

        lines.append("=" * 60)
        lines.append(
            f"Results: {self.ok_count} ok, {self.warning_count} warning, "
            f"{self.error_count} error, {self.skipped_count} skipped"
        )
        lines.append(f"Basic use ready: {'Yes' if self.ready_for_basic_use else 'No'}")
        lines.append(f"Advanced use ready: {'Yes' if self.ready_for_advanced_use else 'No'}")
        return "\n".join(lines)

    # ---- HTML / markdown output (GUI) ----

    def to_html(self) -> str:
        """Format the report as an HTML block for QTextBrowser display."""
        color_map = {
            "ok": "#2e7d32",
            "warning": "#f57f17",
            "error": "#d32f2f",
            "skipped": "#9e9e9e",
        }
        rows = []
        for c in self.checks:
            icon = self.STATUS_ICON.get(c.status, "  ")
            color = color_map.get(c.status, "#333")
            row = (
                f'<tr>'
                f'<td style="color:{color};white-space:nowrap;padding-right:12px;">{icon}</td>'
                f'<td style="font-weight:bold;white-space:nowrap;padding-right:16px;">{c.name}</td>'
                f'<td style="color:#555;">{c.message}</td>'
                f'</tr>'
            )
            if c.suggestion:
                row += (
                    f'<tr>'
                    f'<td></td>'
                    f'<td></td>'
                    f'<td style="color:#999;font-size:9pt;padding-bottom:4px;">'
                    f'→ {c.suggestion}</td>'
                    f'</tr>'
                )
            rows.append(row)

        summary = (
            f'<p style="color:#666;margin-top:12px;">'
            f'<b>Results:</b> {self.ok_count} ok, {self.warning_count} warning, '
            f'{self.error_count} error, {self.skipped_count} skipped &nbsp;|&nbsp; '
            f'Basic: <b style="color:{"#2e7d32" if self.ready_for_basic_use else "#d32f2f"};">'
            f'{"Yes" if self.ready_for_basic_use else "No"}</b> &nbsp;|&nbsp; '
            f'Advanced: <b style="color:{"#2e7d32" if self.ready_for_advanced_use else "#d32f2f"};">'
            f'{"Yes" if self.ready_for_advanced_use else "No"}</b>'
            f'</p>'
        )

        return (
            f'<table style="font-size:10pt;">{" ".join(rows)}</table>'
            f'{summary}'
        )
