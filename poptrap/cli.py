"""Command-line bootstrap and runtime startup sequence for POPTRAP."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from poptrap import APP_NAME, __version__
from poptrap.core.environment import (
    EnvironmentReport,
    check_environment,
    enforce_environment,
)
from poptrap.core.exceptions import EnvironmentCheckError, PopTrapError

EXIT_OK: int = 0
EXIT_RUNTIME_ERROR: int = 1
EXIT_ENVIRONMENT_ERROR: int = 2
EXIT_INTERRUPTED: int = 130


def build_parser() -> argparse.ArgumentParser:
    """Create the POPTRAP command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="poptrap",
        description="POPTRAP — cross-platform terminal-first prank experience platform.",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help="Path to a local TOML configuration file.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate configuration and runtime environment, then exit.",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="store_true",
        help="Show application name and version, then exit.",
    )
    return parser


def _print_error(error: PopTrapError | str, *, hint: str | None = None) -> None:
    """Render a concise error message using Rich when available, else stderr."""
    if isinstance(error, PopTrapError):
        message = error.message
        resolved_hint = error.hint if hint is None else hint
    else:
        message = error
        resolved_hint = hint

    try:
        from rich.console import Console
        from rich.text import Text
    except Exception:  # noqa: BLE001
        sys.stderr.write(f"{APP_NAME} error: {message}\n")
        if resolved_hint:
            sys.stderr.write(f"Guidance: {resolved_hint}\n")
        return

    console = Console(stderr=True, highlight=False)
    header = Text()
    header.append(APP_NAME, style="bold cyan")
    header.append("  ERROR  ", style="bold red")
    header.append(message, style="white")
    console.print(header)
    if resolved_hint:
        guide = Text()
        guide.append("Guidance: ", style="dim")
        guide.append(resolved_hint, style="white")
        console.print(guide)


def _render_check_report(
    report: EnvironmentReport,
    *,
    app_name: str,
    app_version: str,
    config_path: Path,
    log_path: Path | None,
    config_warnings: Sequence[str],
) -> None:
    """Render a minimal Rich runtime diagnostic report for ``--check``."""
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text

    console = Console(highlight=False)

    title = Text()
    title.append(app_name.upper(), style="bold cyan")
    title.append(f"  v{app_version}", style="dim")
    console.print(title)
    console.print(Text("─" * 56, style="bright_black"))

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", width=18)
    grid.add_column(style="white")

    pinfo = report.platform
    term = report.terminal

    py_status = "OK" if report.python_ok else f"UNSUPPORTED (requires {report.required_python}+)"
    py_style = "cyan" if report.python_ok else "red"
    grid.add_row("Python", Text(f"{report.python_version} ({py_status})", style=py_style))
    grid.add_row("Platform", f"{pinfo.system_name} {pinfo.release} ({pinfo.architecture})")
    grid.add_row(
        "Terminal",
        f"{term.term} · {term.columns}x{term.rows} · {term.encoding} · color={term.color_depth.value}",
    )
    grid.add_row(
        "Interactive TUI",
        Text(
            "Ready" if term.supports_tui else "Unavailable in current stream",
            style="cyan" if term.supports_tui else "yellow",
        ),
    )

    for dep in report.dependencies:
        dep_label = dep.installed_version or "missing"
        dep_state = "OK" if dep.ok else "FAIL"
        grid.add_row(
            f"Dep: {dep.distribution}",
            Text(f"{dep_label} ({dep_state})", style="white" if dep.ok else "red"),
        )

    grid.add_row("Config File", str(config_path))
    grid.add_row("Log File", str(log_path) if log_path is not None else "disabled")
    console.print(grid)

    all_warnings = [*config_warnings, *report.warnings]
    if all_warnings:
        console.print()
        for item in all_warnings:
            line = Text()
            line.append("Notice: ", style="yellow")
            line.append(item, style="white")
            console.print(line)

    if report.errors:
        console.print()
        for err in report.errors:
            line = Text()
            line.append("Error: ", style="bold red")
            line.append(err, style="white")
            console.print(line)


def main(argv: Sequence[str] | None = None) -> int:
    """Execute the POPTRAP startup lifecycle and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.version and args.config is None:
        # Fast path when no custom configuration file was provided; still verify
        # default.toml if dependencies are present so configured name/version show.
        try:
            from rich.console import Console

            from poptrap.core.config import load_config

            cfg = load_config().config
            Console(highlight=False).print(
                f"[bold cyan]{cfg.app.name}[/bold cyan] {cfg.app.version}"
            )
        except Exception:  # noqa: BLE001
            sys.stdout.write(f"{APP_NAME} {__version__}\n")
        return EXIT_OK

    # 1. Verify runtime environment (Python version, dependencies, platform, terminal).
    require_interactive = not (args.check or args.version)
    env_report = check_environment(require_interactive=require_interactive)

    if not args.check:
        try:
            enforce_environment(env_report, require_tui=require_interactive)
        except EnvironmentCheckError as exc:
            _print_error(exc)
            return EXIT_ENVIRONMENT_ERROR
    elif not env_report.core_ok:
        # In --check mode, report missing Python/dependencies clearly without crashing.
        for err in env_report.errors:
            _print_error(
                err,
                hint="Install Python 3.11+ and run 'pip install -r requirements.txt'.",
            )
        return EXIT_ENVIRONMENT_ERROR

    # 2. Load and validate configuration (gracefully handles missing/malformed/invalid files).
    from poptrap.core.config import load_config
    from poptrap.core.logging_setup import (
        configure_logging,
        get_logger,
        shutdown_logging,
    )

    config_result = load_config(args.config)
    cfg = config_result.config

    if args.version:
        from rich.console import Console

        Console(highlight=False).print(
            f"[bold cyan]{cfg.app.name}[/bold cyan] {cfg.app.version}"
        )
        return EXIT_OK

    # 3. Initialize local logging subsystem.
    log_state = configure_logging(cfg.logging.level)
    logger = get_logger("startup")

    try:
        logger.info(
            "Initializing %s v%s on %s %s (%s, Python %s)",
            cfg.app.name,
            cfg.app.version,
            env_report.platform.system_name,
            env_report.platform.release,
            env_report.platform.architecture,
            env_report.python_version,
        )
        if log_state.warning:
            logger.warning("%s", log_state.warning)
        for warning in config_result.warnings:
            logger.warning("Configuration: %s", warning)
        for warning in env_report.warnings:
            logger.warning("Environment: %s", warning)

        if args.check:
            _render_check_report(
                env_report,
                app_name=cfg.app.name,
                app_version=cfg.app.version,
                config_path=config_result.config_path,
                log_path=log_state.log_path,
                config_warnings=config_result.warnings,
            )
            return EXIT_OK if env_report.core_ok else EXIT_ENVIRONMENT_ERROR

        # 4 & 5. Launch the interactive POPTRAP terminal interface with branding.
        from poptrap.app import PopTrapApp

        app = PopTrapApp(
            config_result=config_result,
            platform_info=env_report.platform,
            terminal_capabilities=env_report.terminal,
        )
        result = app.run()
        exit_code = int(result) if isinstance(result, int) else EXIT_OK
        logger.info("Application exited cleanly with code %d.", exit_code)
        return exit_code

    except KeyboardInterrupt:
        logger.info("Interrupted by user signal; shutting down cleanly.")
        return EXIT_INTERRUPTED
    except PopTrapError as exc:
        logger.error("Application error: %s", exc.message)
        _print_error(exc)
        return EXIT_RUNTIME_ERROR
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unhandled fatal error during execution")
        _print_error(
            f"Unexpected runtime error ({type(exc).__name__}): {exc}",
            hint=(
                f"Inspect log file '{log_state.log_path}' for details."
                if log_state.log_path
                else "Re-run with 'python poptrap.py --check' to verify the environment."
            ),
        )
        return EXIT_RUNTIME_ERROR
    finally:
        shutdown_logging()
