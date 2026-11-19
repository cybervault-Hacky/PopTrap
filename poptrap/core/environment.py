"""Runtime environment, Python version, dependency, and terminal verification.

Uses only the Python standard library so startup diagnostics always work even
when third-party dependencies have not been installed yet.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from poptrap.core.exceptions import (
    MissingDependencyError,
    UnsupportedPythonError,
    UnsupportedTerminalError,
)
from poptrap.utils.platform import (
    PlatformInfo,
    TerminalCapabilities,
    detect_platform,
    detect_terminal_capabilities,
)

MIN_PYTHON_VERSION: tuple[int, int] = (3, 11)

_VERSION_NUMBERS_RE = re.compile(r"^\s*(\d+)(?:\.(\d+))?(?:\.(\d+))?")

#: Exact command that installs the verified runtime profile. Reported verbatim so a
#: broken or partial install has one unambiguous repair step.
LOCKED_INSTALL_COMMAND: str = (
    "python -m pip install --require-hashes --only-binary=:all: -r requirements/runtime.lock"
)

#: Android Termux is recognized as a platform but is not a verified install or runtime
#: target. ``pydantic-core`` (a locked runtime dependency) publishes no wheel for
#: Termux's bare ``linux_aarch64`` platform tag, so the hash-locked, wheel-only install
#: cannot complete there without a Rust >= 1.88 plus maturin source build that this
#: project has not performed. Report this instead of implying support.
TERMUX_UNSUPPORTED_NOTICE: str = (
    "Android Termux is not a supported install or runtime target for this release. "
    "The locked pydantic-core dependency publishes no wheel for Termux's 'linux_aarch64' "
    "platform tag, so the wheel-only hash-locked install cannot complete there without an "
    "unverified Rust >= 1.88 and maturin source build. Install POPTRAP on a supported "
    "Linux host instead; an Android recipient can open a consensual invitation in a "
    "browser from that host. See docs/compatibility.md and docs/installation.md."
)


@dataclass(frozen=True, slots=True)
class DependencySpec:
    """Required third-party package specification."""

    distribution: str
    import_name: str
    min_version: tuple[int, int, int]

    @property
    def min_version_str(self) -> str:
        """Formatted minimum version string (``major.minor.patch``)."""
        return f"{self.min_version[0]}.{self.min_version[1]}.{self.min_version[2]}"


REQUIRED_DEPENDENCIES: tuple[DependencySpec, ...] = (
    DependencySpec(distribution="textual", import_name="textual", min_version=(8, 2, 8)),
    DependencySpec(distribution="rich", import_name="rich", min_version=(14, 2, 0)),
    DependencySpec(distribution="pydantic", import_name="pydantic", min_version=(2, 9, 0)),
    DependencySpec(distribution="tomli-w", import_name="tomli_w", min_version=(1, 0, 0)),
    DependencySpec(distribution="fastapi", import_name="fastapi", min_version=(0, 164, 0)),
    DependencySpec(distribution="starlette", import_name="starlette", min_version=(1, 6, 0)),
    DependencySpec(distribution="h11", import_name="h11", min_version=(0, 16, 0)),
    DependencySpec(distribution="uvicorn", import_name="uvicorn", min_version=(0, 54, 0)),
    DependencySpec(distribution="websockets", import_name="websockets", min_version=(15, 0, 0)),
    DependencySpec(distribution="ifaddr", import_name="ifaddr", min_version=(0, 2, 0)),
    DependencySpec(distribution="anyio", import_name="anyio", min_version=(4, 14, 2)),
    DependencySpec(distribution="idna", import_name="idna", min_version=(3, 15, 0)),
    DependencySpec(distribution="markdown-it-py", import_name="markdown_it", min_version=(2, 2, 0)),
    DependencySpec(distribution="opentelemetry-api", import_name="opentelemetry.trace", min_version=(1, 45, 2)),
    DependencySpec(distribution="pygments", import_name="pygments", min_version=(2, 20, 0)),
    DependencySpec(distribution="segno", import_name="segno", min_version=(1, 6, 0)),
)


@dataclass(frozen=True, slots=True)
class DependencyStatus:
    """Verification outcome for a single runtime dependency."""

    distribution: str
    import_name: str
    required_version: str
    installed_version: str | None
    available: bool
    compatible: bool
    detail: str | None = None

    @property
    def ok(self) -> bool:
        """Return True when the package is importable and meets the minimum version."""
        return self.available and self.compatible


@dataclass(frozen=True, slots=True)
class EnvironmentReport:
    """Aggregated runtime environment check result."""

    python_ok: bool
    python_version: str
    required_python: str
    platform: PlatformInfo
    terminal: TerminalCapabilities
    dependencies: tuple[DependencyStatus, ...]
    warnings: tuple[str, ...]
    errors: tuple[str, ...]

    @property
    def dependencies_ok(self) -> bool:
        """Return True when all required packages are installed and compatible."""
        return all(dep.ok for dep in self.dependencies)

    @property
    def core_ok(self) -> bool:
        """Return True when Python and all package dependencies satisfy requirements."""
        return self.python_ok and self.dependencies_ok

    @property
    def can_launch_tui(self) -> bool:
        """Return True when the interactive TUI can be launched in this session."""
        return self.core_ok and self.terminal.supports_tui


def parse_version_tuple(version_str: str) -> tuple[int, int, int] | None:
    """Extract leading ``(major, minor, patch)`` integers from a PEP 440 string."""
    match = _VERSION_NUMBERS_RE.match(version_str)
    if match is None:
        return None
    major = int(match.group(1))
    minor = int(match.group(2) or 0)
    patch = int(match.group(3) or 0)
    return (major, minor, patch)


def check_python_version(
    version_info: tuple[int, int, int] | None = None,
) -> tuple[bool, str]:
    """Validate that the active Python interpreter meets ``MIN_PYTHON_VERSION``."""
    if version_info is None:
        vi = sys.version_info
        current = (int(vi[0]), int(vi[1]), int(vi[2]))
    else:
        current = (int(version_info[0]), int(version_info[1]), int(version_info[2]))
    formatted = f"{current[0]}.{current[1]}.{current[2]}"
    is_supported = (current[0], current[1]) >= MIN_PYTHON_VERSION
    return is_supported, formatted


def _default_get_version(distribution: str, module: object) -> str | None:
    """Resolve installed package version via package metadata or ``__version__``."""
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        attr = getattr(module, "__version__", None)
        return str(attr) if isinstance(attr, str) and attr.strip() else None


def check_dependencies(
    specs: Sequence[DependencySpec] = REQUIRED_DEPENDENCIES,
    *,
    importer: Callable[[str], object] | None = None,
    version_lookup: Callable[[str, object], str | None] | None = None,
) -> tuple[DependencyStatus, ...]:
    """Verify each required dependency is importable and version-compatible."""
    do_import = importlib.import_module if importer is None else importer
    get_version = _default_get_version if version_lookup is None else version_lookup

    results: list[DependencyStatus] = []
    for spec in specs:
        try:
            module = do_import(spec.import_name)
        except Exception as exc:  # noqa: BLE001 - capture broken installations cleanly
            results.append(
                DependencyStatus(
                    distribution=spec.distribution,
                    import_name=spec.import_name,
                    required_version=spec.min_version_str,
                    installed_version=None,
                    available=False,
                    compatible=False,
                    detail=f"Not importable ({type(exc).__name__}: {exc})",
                )
            )
            continue

        installed = get_version(spec.distribution, module)
        if installed is None:
            results.append(
                DependencyStatus(
                    distribution=spec.distribution,
                    import_name=spec.import_name,
                    required_version=spec.min_version_str,
                    installed_version=None,
                    available=True,
                    compatible=True,
                    detail="Installed version metadata unavailable",
                )
            )
            continue

        parsed = parse_version_tuple(installed)
        if parsed is None:
            compatible = True
            detail: str | None = f"Unrecognized version format '{installed}'"
        elif parsed < spec.min_version:
            compatible = False
            detail = (
                f"Installed {installed} is older than required >={spec.min_version_str}"
            )
        else:
            compatible = True
            detail = None

        results.append(
            DependencyStatus(
                distribution=spec.distribution,
                import_name=spec.import_name,
                required_version=spec.min_version_str,
                installed_version=installed,
                available=True,
                compatible=compatible,
                detail=detail,
            )
        )

    return tuple(results)


def check_environment(
    *,
    require_interactive: bool = True,
    env: Mapping[str, str] | None = None,
    platform_info: PlatformInfo | None = None,
    terminal_capabilities: TerminalCapabilities | None = None,
    dependency_specs: Sequence[DependencySpec] = REQUIRED_DEPENDENCIES,
    importer: Callable[[str], object] | None = None,
    version_lookup: Callable[[str, object], str | None] | None = None,
) -> EnvironmentReport:
    """Perform a complete, non-destructive host environment assessment."""
    pinfo = detect_platform(env=env) if platform_info is None else platform_info
    python_ok, python_version = check_python_version(pinfo.python_version_tuple)
    required_python = f"{MIN_PYTHON_VERSION[0]}.{MIN_PYTHON_VERSION[1]}"

    terminal = (
        detect_terminal_capabilities(pinfo, env=env)
        if terminal_capabilities is None
        else terminal_capabilities
    )
    dependencies = check_dependencies(
        dependency_specs,
        importer=importer,
        version_lookup=version_lookup,
    )

    errors: list[str] = []
    warnings: list[str] = []

    if not python_ok:
        errors.append(
            f"Python {required_python}+ is required, but Python {python_version} is running."
        )

    if pinfo.is_termux:
        warnings.append(TERMUX_UNSUPPORTED_NOTICE)

    for dep in dependencies:
        if not dep.available:
            errors.append(
                f"Missing required dependency '{dep.distribution}' (>={dep.required_version})."
            )
        elif not dep.compatible:
            errors.append(
                f"Incompatible dependency '{dep.distribution}': "
                f"found {dep.installed_version}, requires >={dep.required_version}."
            )
        elif dep.detail:
            warnings.append(f"{dep.distribution}: {dep.detail}.")

    if require_interactive:
        errors.extend(terminal.blocking_issues)

    warnings.extend(terminal.warnings)

    return EnvironmentReport(
        python_ok=python_ok,
        python_version=python_version,
        required_python=required_python,
        platform=pinfo,
        terminal=terminal,
        dependencies=dependencies,
        warnings=tuple(warnings),
        errors=tuple(errors),
    )


def enforce_environment(
    report: EnvironmentReport,
    *,
    require_tui: bool = True,
) -> None:
    """Raise a typed ``EnvironmentCheckError`` when mandatory checks fail."""
    if not report.python_ok:
        raise UnsupportedPythonError(
            f"POPTRAP requires Python {report.required_python} or newer "
            f"(detected Python {report.python_version}).",
            hint=f"Install Python {report.required_python}+ and recreate your virtual environment.",
        )

    failing_deps = [dep for dep in report.dependencies if not dep.ok]
    if failing_deps:
        names = tuple(
            f"{dep.distribution}>={dep.required_version}" for dep in failing_deps
        )
        listed = ", ".join(names)
        raise MissingDependencyError(
            f"Missing or incompatible runtime dependencies: {listed}.",
            missing=names,
            hint=(
                "Activate the Python 3.11 virtual environment you installed into, run this "
                "from the POPTRAP source or bundle directory, and repeat the check: "
                f"'{LOCKED_INSTALL_COMMAND}'. Never use 'sudo pip' or the system interpreter."
            ),
        )

    if require_tui and not report.terminal.supports_tui:
        primary = (
            report.terminal.blocking_issues[0]
            if report.terminal.blocking_issues
            else "Terminal does not support interactive full-screen rendering."
        )
        raise UnsupportedTerminalError(
            primary,
            reasons=report.terminal.blocking_issues,
            hint="Open an interactive terminal and run 'python -m poptrap' in your installed environment.",
        )
