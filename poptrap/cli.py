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
from poptrap.core.exceptions import ConfigurationError, EnvironmentCheckError, PopTrapError
from poptrap.utils.paths import get_default_config_path, get_log_file_path, get_user_config_path

EXIT_OK: int = 0
EXIT_RUNTIME_ERROR: int = 1
EXIT_ENVIRONMENT_ERROR: int = 2
EXIT_CONFIG_ERROR: int = 3
EXIT_INTERRUPTED: int = 130

# Bounds for the ``--check`` label column. The widest real labels are dependency
# names such as "Dep: opentelemetry-api", which the previous fixed width of 18
# wrapped mid-word, detaching a version from its own row.
CHECK_LABEL_MIN_WIDTH: int = 18
CHECK_LABEL_MAX_WIDTH: int = 32


def build_parser() -> argparse.ArgumentParser:
    """Create the POPTRAP command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="poptrap",
        description="POPTRAP — local, consent-first browser simulations from your terminal.",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help="Path to a local TOML configuration file.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_true",
        help="Report environment readiness and configuration warnings without writing files.",
    )
    mode.add_argument(
        "--validate-config",
        action="store_true",
        help="Strictly validate the selected configuration, without starting the app or writing files.",
    )
    mode.add_argument(
        "--init-config",
        action="store_true",
        help="Create a new user configuration from bundled defaults; never overwrite an existing file.",
    )
    mode.add_argument(
        "-v",
        "--version",
        action="store_true",
        help="Show the installed program version, then exit.",
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
    title.append(f"  v{__version__}", style="dim")
    console.print(title)
    console.print(Text("─" * 56, style="bright_black"))

    dep_rows = tuple(
        (
            f"Dep: {dep.distribution}",
            Text(
                f"{dep.installed_version or 'missing'} ({'OK' if dep.ok else 'FAIL'})",
                style="white" if dep.ok else "red",
            ),
        )
        for dep in report.dependencies
    )
    # Size the label column from its real content so long distribution names stay
    # on the same row as their version, and ellipsize only in very narrow output.
    label_width = min(
        CHECK_LABEL_MAX_WIDTH,
        max(
            CHECK_LABEL_MIN_WIDTH,
            *(len(label) for label in ("Interactive TUI", "Config File", *(label for label, _ in dep_rows))),
        ),
    )
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", width=label_width, no_wrap=True, overflow="ellipsis")
    grid.add_column(style="white")

    pinfo = report.platform
    term = report.terminal

    py_status = "OK" if report.python_ok else f"UNSUPPORTED (requires {report.required_python}+)"
    py_style = "cyan" if report.python_ok else "red"
    grid.add_row("Python", Text(f"{report.python_version} ({py_status})", style=py_style))
    grid.add_row("Platform", Text(f"{pinfo.system_name} {pinfo.release} ({pinfo.architecture})"))
    grid.add_row(
        "Terminal",
        Text(f"{term.term} · {term.columns}x{term.rows} · {term.encoding} · color={term.color_depth.value}"),
    )
    grid.add_row(
        "Interactive TUI",
        Text(
            "Ready" if term.supports_tui else "Unavailable in current stream",
            style="cyan" if term.supports_tui else "yellow",
        ),
    )

    for label, value in dep_rows:
        grid.add_row(label, value)

    grid.add_row("Config File", Text(str(config_path)))
    grid.add_row("Log File", Text(str(log_path) if log_path is not None else "disabled"))
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


def _configure(path: Path | None, *, initialize: bool) -> int:
    """Handle explicit initialization or validation without starting app services."""
    from poptrap.core.config import load_config, load_default_config, save_config

    try:
        target = get_user_config_path() if path is None else path.expanduser()
        if initialize:
            saved = save_config(load_default_config(strict=True), target, overwrite=False)
            print(f"Created user configuration: {saved}")
        elif path is None and not target.exists() and not target.is_symlink():
            load_default_config(strict=True)
            print("Bundled defaults are valid. No user configuration has been saved.")
        else:
            # Explicitly require the packaged defaults, even if they are missing.
            load_config(target, default_path=get_default_config_path(), strict=True)
            print("Configuration is valid.")
    except (ConfigurationError, OSError, RuntimeError) as exc:
        _print_error(exc if isinstance(exc, ConfigurationError) else str(exc))
        return EXIT_CONFIG_ERROR
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """Execute the POPTRAP startup lifecycle and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.version:
        # A user preference file (or a missing dependency) cannot change the
        # installed version. Keep this suitable for scripts and support reports.
        print(f"{APP_NAME} {__version__}")
        return EXIT_OK

    interactive = not (args.check or args.init_config or args.validate_config)
    env_report = check_environment(require_interactive=interactive)
    try:
        enforce_environment(env_report, require_tui=interactive)
    except EnvironmentCheckError as exc:
        _print_error(exc)
        return EXIT_ENVIRONMENT_ERROR

    if args.init_config or args.validate_config:
        return _configure(args.config, initialize=args.init_config)

    from poptrap.core.config import load_config

    config_result = load_config(args.config)
    cfg = config_result.config
    if args.check:
        _render_check_report(
            env_report, app_name=cfg.app.name, config_path=config_result.config_path,
            log_path=get_log_file_path(), config_warnings=config_result.warnings,
        )
        return EXIT_OK

    from poptrap.core.logging_setup import configure_logging, get_logger, shutdown_logging

    # 3. Initialize local logging subsystem.
    log_state = configure_logging(cfg.logging.level)
    logger = get_logger("startup")

    try:
        logger.info(
            "Initializing %s v%s on %s %s (%s, Python %s)",
            cfg.app.name,
            __version__,
            env_report.platform.system_name,
            env_report.platform.release,
            env_report.platform.architecture,
            env_report.python_version,
        )
        if log_state.warning:
            logger.warning("%s", log_state.warning)
        if config_result.warnings:
            logger.warning("Configuration produced %d notice(s). Run --check for details.", len(config_result.warnings))
        if env_report.warnings:
            logger.warning("Environment produced %d notice(s). Run --check for details.", len(env_report.warnings))

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
        logger.error("Application error (%s).", type(exc).__name__)
        _print_error(exc)
        return EXIT_RUNTIME_ERROR
    except Exception as exc:  # noqa: BLE001
        logger.error("Unhandled application error (%s).", type(exc).__name__)
        _print_error(
            f"Unexpected runtime error ({type(exc).__name__}): {exc}",
            hint="Run 'poptrap --check' to verify the environment.",
        )
        return EXIT_RUNTIME_ERROR
    finally:
        shutdown_logging()
