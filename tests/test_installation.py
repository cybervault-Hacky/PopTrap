"""Fresh-installation, dependency-lock, CLI-startup and runtime-import verification.

These checks are offline and deterministic so they run in every regression pass.
They validate what a fresh install consumes (hash locks, packaging metadata,
bundled assets), the dependency declarations those artifacts must agree with, and
the CLI surface a user exercises immediately after installing.

The networked end-to-end proof — creating clean virtual environments, downloading
the locked wheels with hash verification, building and installing the wheel, then
running every documented command — lives in ``tools/verify_install.py``. Set
``POPTRAP_VERIFY_INSTALL=1`` to have pytest drive that harness as well.
"""

from __future__ import annotations

import ast
import importlib
import importlib.metadata
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

from poptrap import APP_NAME, __version__, cli
from poptrap.core.environment import (
    LOCKED_INSTALL_COMMAND,
    MIN_PYTHON_VERSION,
    REQUIRED_DEPENDENCIES,
    TERMUX_UNSUPPORTED_NOTICE,
    DependencySpec,
    check_environment,
    enforce_environment,
)
from poptrap.core.exceptions import MissingDependencyError
from poptrap.utils.paths import (
    get_default_config_path,
    get_log_file_path,
    get_user_config_path,
    get_user_presets_dir,
)
from poptrap.utils.platform import (
    OperatingSystem,
    detect_platform,
    detect_terminal_capabilities,
)

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS_DIR = ROOT / "requirements"
RUNTIME_LOCK = REQUIREMENTS_DIR / "runtime.lock"
TEST_LOCK = REQUIREMENTS_DIR / "test.lock"
BUILD_LOCK = REQUIREMENTS_DIR / "build.lock"
ALL_LOCKS = (RUNTIME_LOCK, TEST_LOCK, BUILD_LOCK)
ENTRY_SCRIPT = ROOT / "poptrap.py"

PIN_LINE_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[^\s;]+)\s*(?P<marker>;.*?)?\s*(?P<hashes>--hash.*)$"
)
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
INCLUDE_RE = re.compile(r"^-r\s+(?P<path>\S+)\s*$")

#: Modules a host may optionally provide. They must stay lazily imported behind an
#: availability probe and out of the verified runtime profile, because POPTRAP has
#: not certified them.
OPTIONAL_THIRD_PARTY_MODULES: frozenset[str] = frozenset({"bleak"})

#: Tokens that must never appear in a lock file: they would let an install bypass
#: the pinned hashes or pull code from somewhere other than the default index.
UNSAFE_LOCK_TOKENS: tuple[str, ...] = (
    "--index-url",
    "--extra-index-url",
    "--no-index",
    "--trusted-host",
    "--find-links",
    "-e ",
    "http://",
    "https://",
    "file://",
)


def canonical(name: str) -> str:
    """Normalize a distribution name per PEP 503."""
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_lock(path: Path) -> dict[str, dict[str, object]]:
    """Parse a lock file into pins keyed by canonical distribution name."""
    text = path.read_text(encoding="utf-8").replace("\\\n", " ")
    pins: dict[str, dict[str, object]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-r "):
            continue
        match = PIN_LINE_RE.match(line)
        assert match is not None, f"{path.name}: not an exact hash-pinned requirement: {line[:90]!r}"
        hashes = re.findall(r"--hash=(sha256:[0-9a-fA-F]+)", match.group("hashes"))
        key = canonical(match.group("name"))
        assert key not in pins, f"{path.name}: duplicate pin for {key}"
        pins[key] = {
            "name": match.group("name"),
            "version": match.group("version"),
            "marker": (match.group("marker") or "").lstrip(";").strip(),
            "hashes": hashes,
        }
    return pins


def lock_includes(path: Path) -> list[str]:
    """Return the ``-r`` includes of a lock file."""
    return [
        INCLUDE_RE.match(line.strip()).group("path")
        for line in path.read_text(encoding="utf-8").splitlines()
        if INCLUDE_RE.match(line.strip())
    ]


def project_metadata() -> dict:
    """Read ``pyproject.toml``."""
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def declared_requirements(texts: list[str]) -> dict[str, Requirement]:
    """Parse PEP 508 requirement strings into a canonical-name mapping."""
    return {canonical(requirement.name): requirement for requirement in map(Requirement, texts)}


def import_roots_by_depth() -> tuple[set[str], set[str]]:
    """Return module-level and function-local import roots across the package."""
    module_level: set[str] = set()
    function_local: set[str] = set()

    class Collector(ast.NodeVisitor):
        def __init__(self) -> None:
            self.depth = 0

        def _enter(self, node: ast.AST) -> None:
            self.depth += 1
            self.generic_visit(node)
            self.depth -= 1

        visit_FunctionDef = _enter
        visit_AsyncFunctionDef = _enter

        def _record(self, root: str) -> None:
            (function_local if self.depth else module_level).add(root)

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                self._record(alias.name.split(".")[0])

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            if node.level == 0 and node.module:
                self._record(node.module.split(".")[0])

    for source in sorted((ROOT / "poptrap").rglob("*.py")):
        Collector().visit(ast.parse(source.read_text(encoding="utf-8"), filename=str(source)))
    return module_level, function_local


def third_party(roots: set[str]) -> set[str]:
    """Keep only roots that are neither standard library nor POPTRAP itself."""
    return {root for root in roots if root not in sys.stdlib_module_names and root != "poptrap"}


def isolated_env(directory: Path) -> dict[str, str]:
    """Build a subprocess environment with POPTRAP's user data redirected."""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.update({
        "POPTRAP_CONFIG_DIR": str(directory / "config"),
        "POPTRAP_PRESETS_DIR": str(directory / "presets"),
        "POPTRAP_LOG_DIR": str(directory / "logs"),
        "NO_COLOR": "1",
        "TERM": "xterm-256color",
    })
    return env


class TestLockFileIntegrity:
    """The hash locks a fresh install consumes must stay exact, complete and safe."""

    def test_lock_files_exist_and_pin_exact_versions(self) -> None:
        for lock in ALL_LOCKS:
            assert lock.is_file(), lock
        runtime = parse_lock(RUNTIME_LOCK)
        assert len(runtime) >= 20
        for key, pin in runtime.items():
            assert Version(pin["version"]), key
            assert pin["marker"] == "", f"{key}: runtime pins must not depend on environment markers"
            assert pin["hashes"], f"{key}: --require-hashes needs at least one hash"
            for digest in pin["hashes"]:
                assert SHA256_RE.fullmatch(digest), f"{key}: malformed hash {digest}"

    def test_runtime_lock_is_self_contained_and_test_lock_extends_it(self) -> None:
        assert lock_includes(RUNTIME_LOCK) == []
        assert lock_includes(TEST_LOCK) == ["runtime.lock"]
        assert lock_includes(BUILD_LOCK) == []
        runtime = parse_lock(RUNTIME_LOCK)
        combined = {**runtime, **parse_lock(TEST_LOCK)}
        for key, pin in runtime.items():
            assert combined[key]["version"] == pin["version"], key
            assert combined[key]["hashes"] == pin["hashes"], key

    def test_locks_forbid_indexes_urls_and_editable_requirements(self) -> None:
        for lock in ALL_LOCKS:
            text = lock.read_text(encoding="utf-8")
            found = [token for token in UNSAFE_LOCK_TOKENS if token in text]
            assert not found, f"{lock.name} would weaken hash verification: {found}"

    def test_build_lock_pins_the_declared_build_backend(self) -> None:
        build = parse_lock(BUILD_LOCK)
        assert "setuptools" in build
        required = project_metadata()["build-system"]["requires"]
        declared = declared_requirements(required)
        assert "setuptools" in declared
        assert Version(build["setuptools"]["version"]) in declared["setuptools"].specifier

    def test_lock_markers_stay_within_the_documented_set(self) -> None:
        markers = {
            str(pin["marker"])
            for lock in ALL_LOCKS
            for pin in parse_lock(lock).values()
            if pin["marker"]
        }
        assert markers <= {'sys_platform == "win32"'}, markers


class TestDependencyDeclarationConsistency:
    """pyproject, requirements.txt, the locks and the CLI health check must agree."""

    def test_every_declared_runtime_dependency_is_pinned_and_satisfied(self) -> None:
        declared = declared_requirements(project_metadata()["project"]["dependencies"])
        runtime = parse_lock(RUNTIME_LOCK)
        assert set(declared) <= set(runtime), sorted(set(declared) - set(runtime))
        for key, requirement in declared.items():
            pinned = Version(runtime[key]["version"])
            assert pinned in requirement.specifier, f"{key}=={pinned} outside {requirement.specifier}"
            assert requirement.marker is None, f"{key}: unexpected marker {requirement.marker}"

    def test_lock_pins_satisfy_requirements_txt_ranges(self) -> None:
        text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
        declared = declared_requirements(lines)
        runtime = parse_lock(RUNTIME_LOCK)
        combined = {**runtime, **parse_lock(TEST_LOCK)}
        assert declared, "requirements.txt declares no ranges"
        for key, requirement in declared.items():
            assert key in combined, f"requirements.txt lists {key}, which no lock pins"
            pinned = Version(combined[key]["version"])
            assert pinned in requirement.specifier, f"{key}=={pinned} outside {requirement.specifier}"

    def test_dev_extra_is_pinned_by_test_lock(self) -> None:
        declared = declared_requirements(project_metadata()["project"]["optional-dependencies"]["dev"])
        tests = parse_lock(TEST_LOCK)
        assert set(declared) <= set(tests), sorted(set(declared) - set(tests))
        for key, requirement in declared.items():
            assert Version(tests[key]["version"]) in requirement.specifier, key

    def test_health_check_floors_match_the_declared_and_locked_versions(self) -> None:
        declared = declared_requirements(project_metadata()["project"]["dependencies"])
        runtime = parse_lock(RUNTIME_LOCK)
        assert len(REQUIRED_DEPENDENCIES) == len(declared)
        for spec in REQUIRED_DEPENDENCIES:
            key = canonical(spec.distribution)
            assert key in declared, f"--check verifies {key}, which pyproject does not declare"
            floor = Version(spec.min_version_str)
            assert floor in declared[key].specifier, f"{key}: health floor {floor} outside {declared[key].specifier}"
            assert Version(runtime[key]["version"]) >= floor, key

    def test_requires_python_matches_the_runtime_guard(self) -> None:
        assert project_metadata()["project"]["requires-python"] == ">=3.11"
        assert MIN_PYTHON_VERSION == (3, 11)
        guard = ENTRY_SCRIPT.read_text(encoding="utf-8")
        assert "_MIN_PYTHON = (3, 11)" in guard
        classifiers = project_metadata()["project"]["classifiers"]
        assert "Programming Language :: Python :: 3.11" in classifiers


class TestPackagingArtifacts:
    """The files a wheel or sdist must ship are present and referenced."""

    def test_package_data_globs_resolve_to_real_files(self) -> None:
        patterns = project_metadata()["tool"]["setuptools"]["package-data"]["poptrap"]
        assert patterns
        for pattern in patterns:
            assert list((ROOT / "poptrap").glob(pattern)), f"package-data glob matches nothing: {pattern}"

    def test_bundled_defaults_ship_with_the_package(self) -> None:
        default = get_default_config_path()
        assert default == ROOT / "poptrap" / "data" / "default.toml"
        assert default.is_file()
        assert (ROOT / "poptrap" / "py.typed").is_file()

    def test_manifest_ships_locks_entry_script_and_tooling(self) -> None:
        manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        for token in ("poptrap.py", "requirements.txt", "runtime.lock", "test.lock", "build.lock", "docs", "tools"):
            assert token in manifest, token

    def test_console_script_and_module_entry_points_exist(self) -> None:
        assert project_metadata()["project"]["scripts"] == {"poptrap": "poptrap.cli:main"}
        assert callable(cli.main)
        assert (ROOT / "poptrap" / "__main__.py").is_file()


class TestRuntimeImports:
    """A locked install must satisfy every import the package performs."""

    def test_every_package_module_imports_on_python_311(self) -> None:
        modules = []
        for source in sorted((ROOT / "poptrap").rglob("*.py")):
            relative = source.relative_to(ROOT).with_suffix("")
            parts = list(relative.parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            modules.append(".".join(parts))
        assert len(modules) > 50
        for name in modules:
            assert importlib.import_module(name), name

    def test_module_level_imports_are_declared_dependencies(self) -> None:
        module_level, function_local = import_roots_by_depth()
        declared = {canonical(spec.distribution) for spec in REQUIRED_DEPENDENCIES}
        declared_roots = {spec.import_name.split(".")[0] for spec in REQUIRED_DEPENDENCIES}
        undeclared = third_party(module_level) - declared_roots
        assert not undeclared, (
            f"Module-level imports missing from pyproject/runtime.lock: {sorted(undeclared)} "
            f"(declared: {sorted(declared)})"
        )

    def test_optional_modules_stay_lazy_guarded_and_unlocked(self) -> None:
        module_level, function_local = import_roots_by_depth()
        optional = third_party(function_local) - third_party(module_level) - {
            spec.import_name.split(".")[0] for spec in REQUIRED_DEPENDENCIES
        }
        assert optional <= OPTIONAL_THIRD_PARTY_MODULES, (
            f"New function-local import(s) must be declared or listed as optional: {sorted(optional)}"
        )
        runtime = parse_lock(RUNTIME_LOCK)
        locked = set(runtime)
        for name in OPTIONAL_THIRD_PARTY_MODULES:
            assert canonical(name) not in locked, f"{name} must stay out of the verified runtime profile"
            assert canonical(name) not in {canonical(spec.distribution) for spec in REQUIRED_DEPENDENCIES}
            assert name not in third_party(module_level), f"{name} must never be imported at module level"
        base = (ROOT / "poptrap" / "engine" / "bluetooth" / "platform" / "base.py").read_text(encoding="utf-8")
        assert "from bleak import" in base
        assert base.index("from bleak import") > base.index("def run_bleak_discovery")

    def test_declared_dependencies_are_importable_in_this_environment(self) -> None:
        for spec in REQUIRED_DEPENDENCIES:
            module = importlib.import_module(spec.import_name)
            assert module is not None, spec.import_name
            installed = importlib.metadata.version(spec.distribution)
            assert Version(installed) >= Version(spec.min_version_str), spec.distribution


class TestCliStartupFlags:
    """The commands the installation documentation tells a user to run."""

    def test_version_is_exact_and_leaves_no_user_data(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert cli.main(["--version"]) == cli.EXIT_OK
        assert capsys.readouterr().out.strip() == f"{APP_NAME} {__version__}"
        assert not get_user_config_path().exists()
        assert not get_log_file_path().parent.exists()
        assert not get_user_presets_dir().exists()

    def test_check_reports_every_dependency_on_its_own_row(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Regression: long distribution names used to wrap and detach their version."""
        assert cli.main(["--check"]) == cli.EXIT_OK
        rendered = capsys.readouterr().out
        assert f"v{__version__}" in rendered
        for spec in REQUIRED_DEPENDENCIES:
            pattern = rf"^Dep: {re.escape(spec.distribution)}\s+\S.*\((OK|FAIL)\)\s*$"
            assert re.search(pattern, rendered, re.MULTILINE), (
                f"{spec.distribution} is not rendered as one intact row:\n{rendered}"
            )
        assert "Dep: opentelemetry-api" in rendered
        assert "Dep: markdown-it-py" in rendered

    def test_check_is_read_only_and_tolerates_a_noninteractive_stream(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert cli.main(["--check"]) == cli.EXIT_OK
        output = capsys.readouterr().out
        assert "Interactive TUI" in output
        assert not get_user_config_path().exists()
        assert not get_log_file_path().parent.exists()

    def test_validate_config_accepts_bundled_defaults_without_writing(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert cli.main(["--validate-config"]) == cli.EXIT_OK
        assert "Bundled defaults are valid" in capsys.readouterr().out
        assert not get_user_config_path().exists()

    def test_init_config_creates_a_private_file_once(
        self, capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert cli.main(["--init-config"]) == cli.EXIT_OK
        assert "Created user configuration" in capsys.readouterr().out
        path = get_user_config_path()
        assert path.is_file()
        if os.name == "posix":
            assert path.stat().st_mode & 0o777 == 0o600
            assert path.parent.stat().st_mode & 0o777 == 0o700
        saved = path.read_bytes()
        assert cli.main(["--init-config"]) == cli.EXIT_CONFIG_ERROR
        assert path.read_bytes() == saved
        assert cli.main(["--validate-config"]) == cli.EXIT_OK

    def test_init_config_accepts_an_explicit_path(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        target = tmp_path / "custom" / "poptrap.toml"
        assert cli.main(["--init-config", "--config", str(target)]) == cli.EXIT_OK
        assert "Created user configuration" in capsys.readouterr().out
        assert target.is_file()
        assert cli.main(["--validate-config", "--config", str(target)]) == cli.EXIT_OK
        assert not get_user_config_path().exists()

    def test_invalid_configuration_is_reported_with_a_distinct_exit_code(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    ) -> None:
        broken = tmp_path / "broken.toml"
        broken.write_text("[ui\n", encoding="utf-8")
        assert cli.main(["--validate-config", "--config", str(broken)]) == cli.EXIT_CONFIG_ERROR
        assert capsys.readouterr().err.strip()
        assert cli.main(["--check", "--config", str(broken)]) == cli.EXIT_OK

    @pytest.mark.parametrize("arguments", [["--version", "--check"], ["--check", "--init-config"], ["--nope"]])
    def test_conflicting_and_unknown_arguments_are_rejected(self, arguments: list[str]) -> None:
        with pytest.raises(SystemExit) as exit_info:
            cli.main(arguments)
        assert exit_info.value.code == 2

    def test_module_and_script_entry_points_start_in_a_subprocess(self, tmp_path: Path) -> None:
        env = isolated_env(tmp_path)
        expected = f"{APP_NAME} {__version__}"
        for command in (
            [sys.executable, "-m", "poptrap", "--version"],
            [sys.executable, str(ENTRY_SCRIPT), "--version"],
        ):
            result = subprocess.run(command, cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=60)
            assert result.returncode == 0, result.stderr
            assert result.stdout.strip() == expected
        for arguments in (["--check"], ["--validate-config"]):
            result = subprocess.run(
                [sys.executable, "-m", "poptrap", *arguments],
                cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120,
            )
            assert result.returncode == 0, f"{arguments}: {result.stderr}"
            assert result.stdout.strip()

    def test_script_guard_refuses_an_older_interpreter(self, tmp_path: Path) -> None:
        """The documented Python floor is enforced before any dependency import."""
        driver = tmp_path / "guard_probe.py"
        driver.write_text(
            "import importlib.util, sys\n"
            f"spec = importlib.util.spec_from_file_location('poptrap_entry', {str(ENTRY_SCRIPT)!r})\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n"
            "sys.version_info = (3, 10, 14)\n"
            "sys.exit(module._run())\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [sys.executable, str(driver)], cwd=str(tmp_path), capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 2
        assert "Python 3.11+ is required" in result.stderr
        assert "3.10.14" in result.stderr


class TestDependencyFailureGuidance:
    """A broken or partial install must tell the user the exact repair command."""

    def test_missing_dependency_error_carries_the_locked_install_command(self) -> None:
        report = check_environment(
            require_interactive=False,
            dependency_specs=(DependencySpec("textual", "textual", (99, 0, 0)),),
        )
        assert report.dependencies_ok is False
        with pytest.raises(MissingDependencyError) as failure:
            enforce_environment(report, require_tui=False)
        hint = failure.value.hint or ""
        assert LOCKED_INSTALL_COMMAND in hint
        assert "--require-hashes" in LOCKED_INSTALL_COMMAND
        assert "--only-binary=:all:" in LOCKED_INSTALL_COMMAND
        assert "requirements/runtime.lock" in LOCKED_INSTALL_COMMAND
        assert (ROOT / "requirements" / "runtime.lock").is_file()

    def test_the_documented_install_command_names_a_real_lock_file(self) -> None:
        reference = LOCKED_INSTALL_COMMAND.rsplit("-r ", 1)[1].strip()
        assert (ROOT / reference).is_file(), reference


class TestTermuxSupportHonesty:
    """Termux is detected but must never be presented as a supported target."""

    @staticmethod
    def _termux_platform() -> object:
        return detect_platform(
            sys_platform="linux",
            system="Linux",
            release="4.14.190",
            version="#1 SMP PREEMPT",
            machine="aarch64",
            env={"TERMUX_VERSION": "0.118.1", "PREFIX": "/data/data/com.termux/files/usr"},
        )

    def test_termux_is_detected_and_reported_unsupported(self) -> None:
        platform_info = self._termux_platform()
        assert platform_info.os_type is OperatingSystem.TERMUX
        assert platform_info.is_termux is True
        report = check_environment(
            require_interactive=False,
            platform_info=platform_info,
            env={"TERMUX_VERSION": "0.118.1", "TERM": "xterm-256color"},
        )
        assert TERMUX_UNSUPPORTED_NOTICE in report.warnings
        assert "linux_aarch64" in TERMUX_UNSUPPORTED_NOTICE
        assert "pydantic-core" in TERMUX_UNSUPPORTED_NOTICE
        assert report.errors == ()

    def test_supported_hosts_get_no_termux_notice(self) -> None:
        report = check_environment(require_interactive=False, env={"TERM": "xterm-256color"})
        assert report.platform.os_type is OperatingSystem.LINUX
        assert TERMUX_UNSUPPORTED_NOTICE not in report.warnings
        assert not any("Termux" in warning for warning in report.warnings)

    def test_unknown_operating_systems_are_not_told_termux_is_a_target(self) -> None:
        unknown = detect_platform(sys_platform="plan9", system="Plan 9", machine="mips")
        capabilities = detect_terminal_capabilities(
            unknown,
            env={},
            stdin_isatty=True,
            stdout_isatty=True,
            encoding="utf-8",
            terminal_size=(80, 24),
        )
        joined = " ".join(capabilities.warnings)
        assert "Unrecognized operating system" in joined
        assert "verified on Linux" in joined
        assert "not a supported target" in joined


@pytest.fixture(scope="module")
def readme() -> str:
    """Read the README once for the documentation consistency checks."""
    return (ROOT / "README.md").read_text(encoding="utf-8")


class TestInstallationDocumentation:
    """The documented install steps must match the repository's real artifacts."""

    def test_readme_has_an_installation_section_with_the_verified_steps(self, readme: str) -> None:
        assert re.search(r"^##\s+Installation", readme, re.MULTILINE), "README needs an Installation section"
        for token in (
            "git clone https://github.com/cybervault-Hacky/PopTrap",
            "python3.11 -m venv .venv",
            "source .venv/bin/activate",
            "--require-hashes --only-binary=:all: -r requirements/runtime.lock",
            "python -m poptrap --version",
            "python -m poptrap --check",
            "python -m poptrap --validate-config",
            "python -m poptrap --init-config",
            "python -m pip uninstall poptrap",
            "python3.11-venv",
        ):
            assert token in readme, f"README installation section is missing: {token!r}"

    def test_readme_labels_platform_support_honestly(self, readme: str) -> None:
        installation = readme[readme.index("## Installation"):]
        for platform in ("Linux", "Termux", "Windows", "macOS"):
            assert platform in installation, platform
        assert re.search(r"Termux[^\n]*(not supported|Unsupported|unsupported)", installation), (
            "README must state plainly that Termux is not supported"
        )
        assert re.search(r"(Windows|macOS)[^\n]*(not been (run|executed)|unverified|not verified)", installation), (
            "README must label Windows/macOS as instructions that were not executed here"
        )

    def test_documented_requirement_files_exist(self) -> None:
        documents = ("README.md", "CONTRIBUTING.md", "docs/installation.md", "docs/troubleshooting.md",
                     "docs/releasing.md")
        references: Iterator[str] = (
            reference
            for document in documents
            for reference in re.findall(r"-r\s+([A-Za-z0-9_./-]+(?:\.lock|\.txt))", (ROOT / document).read_text(encoding="utf-8"))
        )
        seen = list(references)
        assert seen
        for reference in seen:
            assert (ROOT / reference).is_file() or (REQUIREMENTS_DIR / Path(reference).name).is_file(), reference

    def test_support_documents_carry_the_verified_termux_blocker(self) -> None:
        for document in ("docs/installation.md", "docs/compatibility.md"):
            text = (ROOT / document).read_text(encoding="utf-8")
            assert "linux_aarch64" in text, f"{document} must record the concrete Termux blocker"
            assert re.search(r"Termux[^\n]*not (a )?supported", text, re.IGNORECASE), document


@pytest.mark.skipif(
    os.environ.get("POPTRAP_VERIFY_INSTALL") != "1",
    reason="Set POPTRAP_VERIFY_INSTALL=1 to run the networked fresh-install harness.",
)
class TestNetworkedFreshInstall:
    """Optional end-to-end proof: clean virtual environments and a real locked install."""

    def test_fresh_install_harness_passes(self, tmp_path: Path) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "verify_install.py"), "--stages", "fresh",
             "--workdir", str(tmp_path / "work"), "--python", sys.executable],
            cwd=str(tmp_path), capture_output=True, text=True, timeout=2700,
        )
        assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
        assert "0 failure(s)" in result.stdout
