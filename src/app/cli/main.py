import sys

from src.app.cli.parser import build_parser
from src.app.cli.registry import CommandRegistry
from src.app.cli.commands.diagnostics import register_diagnostics
from src.app.cli.commands.templates import register_templates
from src.app.cli.commands.provenance import register_provenance
from src.app.cli.commands.collections import register_collections
from src.app.cli.commands.jobs import register_jobs
from src.app.cli.commands.process import register_process


def _configure_console_output():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main():
    _configure_console_output()

    parser = build_parser()

    registry = CommandRegistry()
    register_diagnostics(registry)
    register_templates(registry)
    register_provenance(registry)
    register_collections(registry)
    register_jobs(registry)
    register_process(registry)

    args = parser.parse_args()

    exit_code = registry.dispatch(args)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
