#!/usr/bin/env python3
"""Verify a POPTRAP installation end to end from a clean environment.

Stages
------
``structure``
    Lock files, packaging metadata and documented file references agree (offline).
``hashes``
    Every ``--hash`` in every lock matches a digest PyPI actually publishes, and
    files PyPI publishes are not missing from the lock (network).
``platforms``
    A hash-verified wheel exists for every documented target platform, and the
    Android Termux target provably has none, which is why Termux is unsupported
    rather than merely untested (network).
``fresh``
    Build the release bundle, then create clean virtual environments, install the
    documented source-tree and wheel profiles with hash verification, run
    ``pip check``, every documented CLI command, the installed smoke test, and the
    documented uninstall behavior (network).

Run it with the interpreter you want to certify, for example::

    python3.11 tools/verify_install.py                 # every stage
    python3.11 tools/verify_install.py --stages structure
    python3.11 tools/verify_install.py --keep          # inspect the work directory

It uses only the standard library, never touches the real user profile (config,
preset and log directories are redirected into the work directory), and exits
non-zero if any check fails. Warnings never fail a stage on their own.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = ROOT / "requirements"
RUNTIME_LOCK = REQUIREMENTS / "runtime.lock"
TEST_LOCK = REQUIREMENTS / "test.lock"
BUILD_LOCK = REQUIREMENTS / "build.lock"
ALL_LOCKS = (RUNTIME_LOCK, TEST_LOCK, BUILD_LOCK)

Status = Literal["PASS", "FAIL", "WARN"]

PIN_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[^\s;]+)\s*(?P<marker>;.*?)?\s*(?P<hashes>--hash.*)$"
)
HASH_RE = re.compile(r"--hash=sha256:(?P<digest>[0-9a-fA-F]+)")
UNSAFE_TOKENS = (
    "--index-url", "--extra-index-url", "--no-index", "--trusted-host",
    "--find-links", "-e ", "http://", "https://", "file://",
)

#: Documented install targets: (label, pip platform arguments, expected outcome).
#: ``linux_aarch64`` is Termux's platform tag; Android's Bionic libc is not glibc,
#: so manylinux wheels are not compatible there and pip offers no binary at all.
PLATFORM_TARGETS: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    ("Linux x86_64 · CPython 3.11 (verified reference)",
     ("--python-version", "311", "--implementation", "cp", "--abi", "cp311",
      "--platform", "manylinux2014_x86_64"), True),
    ("Linux ARM64 · CPython 3.11 (install level)",
     ("--python-version", "311", "--implementation", "cp", "--abi", "cp311",
      "--platform", "manylinux2014_aarch64"), True),
    ("Windows x86_64 · CPython 3.11 (instructions only)",
     ("--python-version", "311", "--implementation", "cp", "--abi", "cp311",
      "--platform", "win_amd64"), True),
    ("macOS Apple Silicon · CPython 3.11 (instructions only)",
     ("--python-version", "311", "--implementation", "cp", "--abi", "cp311",
      "--platform", "macosx_11_0_arm64"), True),
    ("macOS Intel · CPython 3.11 (instructions only)",
     ("--python-version", "311", "--implementation", "cp", "--abi", "cp311",
      "--platform", "macosx_10_12_x86_64"), True),
    ("Linux x86_64 · CPython 3.12 (install level)",
     ("--python-version", "312", "--implementation", "cp", "--abi", "cp312",
      "--platform", "manylinux2014_x86_64"), True),
    ("Linux x86_64 · CPython 3.13 (install level)",
     ("--python-version", "313", "--implementation", "cp", "--abi", "cp313",
      "--platform", "manylinux2014_x86_64"), True),
    ("Android Termux · CPython 3.11 (must stay unsupported)",
     ("--python-version", "311", "--implementation", "cp", "--abi", "cp311",
      "--platform", "linux_aarch64"), False),
)

CLI_COMMANDS: tuple[tuple[str, tuple[str, ...], int], ...] = (
    ("version", ("--version",), 0),
    ("check", ("--check",), 0),
    ("validate-config", ("--validate-config",), 0),
    ("init-config", ("--init-config",), 0),
    ("init-config again (must not overwrite)", ("--init-config",), 3),
    ("validate-config after init", ("--validate-config",), 0),
)


@dataclass
class Report:
    """Collect stage results so the run ends with one honest summary."""

    rows: list[tuple[str, str, Status, str]] = field(default_factory=list)

    def record(self, stage: str, name: str, status: Status, detail: str = "") -> None:
        self.rows.append((stage, name, status, detail))
        marker = {"PASS": "ok  ", "FAIL": "FAIL", "WARN": "warn"}[status]
        print(f"[{marker}] {stage}: {name}{f' — {detail}' if detail else ''}", flush=True)

    def check(self, stage: str, name: str, condition: bool, detail: str = "", warn_only: bool = False) -> bool:
        if condition:
            self.record(stage, name, "PASS", detail)
        else:
            self.record(stage, name, "WARN" if warn_only else "FAIL", detail)
        return condition

    @property
    def failures(self) -> int:
        return sum(1 for row in self.rows if row[2] == "FAIL")

    @property
    def warnings(self) -> int:
        return sum(1 for row in self.rows if row[2] == "WARN")

    def summarize(self) -> None:
        total = len(self.rows)
        print("\n" + "=" * 72)
        print(f"POPTRAP installation verification: {total - self.failures - self.warnings}/{total} passed, "
              f"{self.warnings} warning(s), {self.failures} failure(s).")
        for stage, name, status, detail in self.rows:
            if status != "PASS":
                print(f"  {status} {stage}: {name} — {detail}")
        print("=" * 72, flush=True)


@dataclass(frozen=True)
class Pin:
    """One normalized lock-file requirement line."""

    name: str
    canonical: str
    version: str
    marker: str
    hashes: tuple[str, ...]


def canonical_name(name: str) -> str:
    """Apply PEP 503 name normalization."""
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_lock(path: Path) -> dict[str, Pin]:
    """Parse a pip requirements/lock file into normalized pins."""
    text = path.read_text(encoding="utf-8").replace("\\\n", " ")
    pins: dict[str, Pin] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-r "):
            continue
        match = PIN_RE.match(line)
        if match is None:
            raise ValueError(f"{path.name}: not an exact, hash-pinned requirement: {line[:90]!r}")
        digests = tuple(digest.lower() for digest in HASH_RE.findall(match.group("hashes")))
        name = match.group("name")
        key = canonical_name(name)
        if key in pins:
            raise ValueError(f"{path.name}: duplicate pin for {key}")
        pins[key] = Pin(
            name=name,
            canonical=key,
            version=match.group("version"),
            marker=(match.group("marker") or "").lstrip(";").strip(),
            hashes=digests,
        )
    return pins


def lock_includes(path: Path) -> list[str]:
    """Return the ``-r`` includes declared by a lock file."""
    return [
        line.strip()[3:].strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("-r ")
    ]


def effective_test_pins() -> dict[str, Pin]:
    """Return runtime plus test pins as a contributor environment resolves them."""
    pins = dict(parse_lock(RUNTIME_LOCK))
    pins.update(parse_lock(TEST_LOCK))
    return pins


def pyproject() -> dict:
    """Read the project metadata."""
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def requirement_parts(text: str) -> tuple[str, str]:
    """Split ``name>=low,<high`` into a canonical name and its specifier text."""
    match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(.*)$", text.strip())
    if match is None:
        raise ValueError(f"Unparsable requirement: {text!r}")
    return canonical_name(match.group(1)), match.group(2).strip()


def version_tuple(text: str) -> tuple[int, ...]:
    """Parse the leading numeric components of a version string."""
    numbers = re.findall(r"\d+", text)
    return tuple(int(number) for number in numbers[:3]) or (0,)


def satisfies(version: str, specifier: str) -> bool:
    """Evaluate a pinned version against a specifier without third-party packages."""
    if not specifier:
        return True
    current = version_tuple(version)
    for clause in specifier.split(","):
        clause = clause.strip()
        match = re.match(r"^(>=|<=|==|!=|~=|>|<)\s*([0-9][0-9a-zA-Z.*!]*)$", clause)
        if match is None:
            raise ValueError(f"Unsupported specifier clause {clause!r}")
        operator, bound = match.group(1), match.group(2)
        target = version_tuple(bound)
        width = max(len(current), len(target))
        left = current + (0,) * (width - len(current))
        right = target + (0,) * (width - len(target))
        if operator == ">=" and not left >= right:
            return False
        if operator == "<=" and not left <= right:
            return False
        if operator == ">" and not left > right:
            return False
        if operator == "<" and not left < right:
            return False
        if operator == "==" and not left == right:
            return False
        if operator == "!=" and left == right:
            return False
        if operator == "~=" and not (left >= right and left[0] == right[0]):
            return False
    return True


def run(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None,
        timeout: int = 900) -> subprocess.CompletedProcess[str]:
    """Run a command, capturing output without inheriting an interactive terminal."""
    return subprocess.run(
        command, cwd=str(cwd) if cwd else None, env=env,
        capture_output=True, text=True, timeout=timeout, check=False,
    )


def venv_python(directory: Path) -> Path:
    """Return the interpreter path inside a virtual environment."""
    return directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def create_venv(directory: Path, interpreter: str, report: Report, stage: str) -> Path | None:
    """Create a clean virtual environment and report the outcome."""
    result = run([interpreter, "-m", "venv", str(directory)], timeout=300)
    python = venv_python(directory)
    if result.returncode != 0 or not python.exists():
        hint = (result.stderr or result.stdout).strip().splitlines()
        detail = hint[-1] if hint else "venv creation failed"
        if "ensurepip" in (result.stderr + result.stdout):
            detail += " (install the distribution's python3-venv/python3.11-venv package)"
        report.record(stage, f"create virtual environment {directory.name}", "FAIL", detail)
        return None
    report.record(stage, f"create virtual environment {directory.name}", "PASS", str(python))
    return python


def install_locked(python: Path, locks: list[Path], report: Report, stage: str, label: str) -> bool:
    """Install hash-verified, wheel-only locked requirements and report the outcome."""
    command = [str(python), "-m", "pip", "install", "--require-hashes", "--only-binary=:all:"]
    command.extend(argument for lock in locks for argument in ("-r", str(lock)))
    result = run(command, timeout=1800)
    ok = result.returncode == 0
    detail = "hash-verified wheel-only install"
    if not ok:
        lines = [line for line in (result.stderr or result.stdout).splitlines() if line.strip()]
        detail = lines[-1][:300] if lines else "pip failed"
    return report.check(stage, label, ok, detail)


def profile_env(workdir: Path, name: str) -> dict[str, str]:
    """Build an environment with POPTRAP's user directories redirected."""
    profile = workdir / name
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.update({
        "POPTRAP_CONFIG_DIR": str(profile / "config"),
        "POPTRAP_PRESETS_DIR": str(profile / "presets"),
        "POPTRAP_LOG_DIR": str(profile / "logs"),
        "TERM": env.get("TERM") or "xterm-256color",
        "NO_COLOR": "1",
    })
    return env


def stage_structure(report: Report) -> None:
    """Validate lock files and packaging metadata offline."""
    stage = "structure"
    metadata = pyproject()
    declared = dict(requirement_parts(text) for text in metadata["project"]["dependencies"])
    extras = {
        canonical_name(re.split(r"[><=!~\[; ]", text)[0]): requirement_parts(text)[1]
        for text in metadata["project"]["optional-dependencies"]["dev"]
    }

    for lock in ALL_LOCKS:
        report.check(stage, f"{lock.name} exists", lock.is_file())

    try:
        runtime = parse_lock(RUNTIME_LOCK)
    except ValueError as exc:
        report.record(stage, "runtime.lock parses", "FAIL", str(exc))
        return
    report.record(stage, "runtime.lock parses", "PASS", f"{len(runtime)} exact pins")

    try:
        tests = parse_lock(TEST_LOCK)
        build = parse_lock(BUILD_LOCK)
    except ValueError as exc:
        report.record(stage, "test/build locks parse", "FAIL", str(exc))
        return
    report.record(stage, "test/build locks parse", "PASS", f"{len(tests)} test + {len(build)} build pins")

    unsafe = [
        f"{lock.name}: {token!r}"
        for lock in ALL_LOCKS
        for token in UNSAFE_TOKENS
        if token in lock.read_text(encoding="utf-8")
    ]
    report.check(stage, "locks declare no index, URL or editable override", not unsafe, "; ".join(unsafe))

    unhashed = [pin.name for pin in (*runtime.values(), *tests.values(), *build.values()) if not pin.hashes]
    report.check(stage, "every pin carries at least one sha256 hash", not unhashed, ", ".join(unhashed))

    malformed = [
        f"{pin.name}:{digest[:12]}"
        for pin in (*runtime.values(), *tests.values(), *build.values())
        for digest in pin.hashes
        if not re.fullmatch(r"[0-9a-f]{64}", digest)
    ]
    report.check(stage, "every hash is a full lowercase sha256 digest", not malformed, ", ".join(malformed))

    report.check(
        stage, "runtime.lock is self-contained (no nested -r includes)",
        not lock_includes(RUNTIME_LOCK), ",".join(lock_includes(RUNTIME_LOCK)),
    )
    report.check(
        stage, "test.lock includes runtime.lock",
        "runtime.lock" in lock_includes(TEST_LOCK), ",".join(lock_includes(TEST_LOCK)),
    )

    missing_pins = sorted(set(declared) - set(runtime))
    report.check(stage, "every declared runtime dependency is pinned", not missing_pins, ", ".join(missing_pins))

    unsatisfied = sorted(
        f"{name}=={runtime[name].version} outside {declared[name]}"
        for name in set(declared) & set(runtime)
        if not satisfies(runtime[name].version, declared[name])
    )
    report.check(stage, "every runtime pin satisfies its declared range", not unsatisfied, "; ".join(unsatisfied))

    missing_dev = sorted(set(extras) - set(tests))
    report.check(stage, "every dev extra is pinned by test.lock", not missing_dev, ", ".join(missing_dev))
    unsatisfied_dev = sorted(
        f"{name}=={tests[name].version} outside {extras[name]}"
        for name in set(extras) & set(tests)
        if not satisfies(tests[name].version, extras[name])
    )
    report.check(stage, "every dev pin satisfies its declared range", not unsatisfied_dev, "; ".join(unsatisfied_dev))

    effective = effective_test_pins()
    absent = sorted(set(runtime) - set(effective))
    drift = sorted(
        f"{name}: runtime {runtime[name].version} vs test {effective[name].version}"
        for name in set(runtime) & set(effective)
        if runtime[name].version != effective[name].version
        or runtime[name].hashes != effective[name].hashes
    )
    report.check(
        stage, "a contributor environment resolves the exact runtime profile",
        not absent and not drift, "; ".join([*absent, *drift]),
    )

    build_requires = metadata["build-system"]["requires"]
    setuptools_pins = [pin for pin in build.values() if pin.canonical == "setuptools"]
    backend_ok = bool(setuptools_pins) and all(
        satisfies(setuptools_pins[0].version, requirement_parts(text)[1])
        for text in build_requires
        if canonical_name(re.split(r"[><=!~\[; ]", text)[0]) == "setuptools"
    )
    report.check(
        stage, "build.lock pins a setuptools backend satisfying build-system.requires",
        backend_ok,
        f"{setuptools_pins[0].version if setuptools_pins else 'missing'} vs {build_requires}",
    )

    unmarked = [pin.name for pin in runtime.values() if pin.marker]
    report.check(stage, "runtime pins carry no environment markers", not unmarked, ", ".join(unmarked))
    markers = [pin.marker for pin in (*tests.values(), *build.values()) if pin.marker]
    report.check(
        stage, "lock markers stay within the documented set",
        all(marker == 'sys_platform == "win32"' for marker in markers), "; ".join(markers),
    )

    package_root = ROOT / "poptrap"
    empty_globs = [
        pattern
        for pattern in metadata["tool"]["setuptools"]["package-data"]["poptrap"]
        if not list(package_root.glob(pattern))
    ]
    report.check(stage, "every package-data glob matches a real file", not empty_globs, ", ".join(empty_globs))

    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    unshipped = [name for name in ("requirements.txt", "runtime.lock", "test.lock", "build.lock") if name not in manifest]
    report.check(stage, "MANIFEST.in ships the requirement files", not unshipped, ", ".join(unshipped))

    report.check(
        stage, "declared requires-python matches the documented 3.11 floor",
        metadata["project"]["requires-python"] == ">=3.11", metadata["project"]["requires-python"],
    )
    report.check(
        stage, "console script entry point is declared",
        metadata["project"]["scripts"].get("poptrap") == "poptrap.cli:main",
        json.dumps(metadata["project"]["scripts"]),
    )

    documented = [
        reference
        for document in ("README.md", "CONTRIBUTING.md", "docs/installation.md", "docs/troubleshooting.md")
        for reference in re.findall(r"-r\s+([A-Za-z0-9_./-]+(?:\.lock|\.txt))", (ROOT / document).read_text(encoding="utf-8"))
    ]
    unresolved = sorted({
        reference for reference in documented
        if not (ROOT / reference).exists() and not (REQUIREMENTS / Path(reference).name).exists()
    })
    report.check(
        stage, "documented -r requirement files exist",
        not unresolved, ", ".join(unresolved),
    )


def fetch_pypi(name: str, version: str, timeout: int = 90) -> dict:
    """Fetch release metadata from PyPI."""
    request = urllib.request.Request(
        f"https://pypi.org/pypi/{name}/{version}/json",
        headers={"User-Agent": "poptrap-verify-install"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https host
        return json.load(response)


def stage_hashes(report: Report) -> None:
    """Confirm every locked hash is a digest PyPI publishes for that exact release."""
    stage = "hashes"
    pins = dict(parse_lock(BUILD_LOCK))
    pins.update(effective_test_pins())
    for key in sorted(pins):
        pin = pins[key]
        try:
            release = fetch_pypi(pin.name, pin.version)
        except Exception as exc:  # noqa: BLE001 - network or PyPI outage
            report.record(stage, f"{pin.name}=={pin.version}", "FAIL", f"PyPI lookup failed: {exc}")
            continue
        published = {entry["digests"]["sha256"].lower(): entry["filename"] for entry in release["urls"]}
        unknown = sorted(set(pin.hashes) - set(published))
        if unknown:
            report.record(
                stage, f"{pin.name}=={pin.version}", "FAIL",
                f"{len(unknown)} locked hash(es) are not published by PyPI "
                f"(a wrong hash makes --require-hashes abort): {unknown[0][:16]}…",
            )
            continue
        uncovered = sorted(set(published) - set(pin.hashes))
        detail = f"{len(pin.hashes)}/{len(published)} published files locked"
        if uncovered:
            names = ", ".join(published[digest] for digest in uncovered[:3])
            report.record(
                stage, f"{pin.name}=={pin.version}", "WARN",
                f"{detail}; {len(uncovered)} unlocked file(s) can break an install on a platform "
                f"that needs them: {names}",
            )
        else:
            report.record(stage, f"{pin.name}=={pin.version}", "PASS", detail)


def stage_platforms(report: Report, workdir: Path, python: str) -> None:
    """Prove wheel availability per documented target with pip's own resolver."""
    stage = "platforms"
    expected_files = len(parse_lock(RUNTIME_LOCK))
    for label, arguments, expected in PLATFORM_TARGETS:
        destination = workdir / "probe" / re.sub(r"[^A-Za-z0-9]+", "-", label)
        destination.mkdir(parents=True, exist_ok=True)
        command = [
            python, "-m", "pip", "download", "--no-deps", "--require-hashes",
            "--only-binary=:all:", "-d", str(destination), "-r", str(RUNTIME_LOCK), *arguments,
        ]
        result = run(command, timeout=1200)
        downloaded = len([path for path in destination.iterdir() if path.is_file()])
        if expected:
            report.check(
                stage, label, result.returncode == 0 and downloaded >= expected_files,
                f"{downloaded} hash-verified wheels" if result.returncode == 0
                else (result.stderr or result.stdout).strip().splitlines()[-1][:200],
            )
        else:
            blocked = result.returncode != 0 and "pydantic-core" in (result.stderr + result.stdout)
            report.check(
                stage, label, blocked,
                "no pydantic-core wheel exists for this platform tag, so the wheel-only locked "
                "install cannot complete (a Rust >= 1.88 plus maturin source build would be needed)"
                if blocked else
                f"unexpected: pip resolved this target ({downloaded} files). Re-evaluate the "
                "Termux support claim in docs/compatibility.md.",
            )
        shutil.rmtree(destination, ignore_errors=True)


def stage_fresh(report: Report, workdir: Path, python: str, epoch: int) -> None:
    """Build the bundle and install both documented profiles into clean environments."""
    stage = "fresh"

    build_python = create_venv(workdir / "build-venv", python, report, stage)
    if build_python is None:
        return
    if not install_locked(build_python, [BUILD_LOCK], report, stage, "install build backend from build.lock"):
        return

    bundle = workdir / "bundle"
    result = run([str(build_python), str(ROOT / "tools" / "build_release.py"),
                  "--epoch", str(epoch), "--output", str(bundle)], cwd=ROOT, timeout=900)
    if not report.check(stage, "build reproducible wheel and sdist bundle", result.returncode == 0,
                        (result.stderr or result.stdout).strip().splitlines()[-1][:200] if result.returncode else str(bundle)):
        return

    sums = (bundle / "SHA256SUMS").read_text(encoding="ascii").splitlines()
    mismatched = []
    for line in sums:
        digest, name = line.split("  ", 1)
        actual = hashlib.file_digest((bundle / name).open("rb"), "sha256").hexdigest()
        if actual != digest:
            mismatched.append(name)
    report.check(stage, "bundle SHA256SUMS verify", not mismatched and len(sums) >= 3,
                 ", ".join(mismatched) or f"{len(sums)} files")

    wheel = next(iter(sorted(bundle.glob("poptrap-*-py3-none-any.whl"))), None)
    sdist = next(iter(sorted(bundle.glob("poptrap-*.tar.gz"))), None)
    if not report.check(stage, "bundle contains a wheel and a source archive", bool(wheel and sdist),
                        f"{wheel.name if wheel else 'no wheel'}, {sdist.name if sdist else 'no sdist'}"):
        return

    # 1. Documented source-tree profile: locked runtime dependencies only.
    source_python = create_venv(workdir / "source-venv", python, report, stage)
    if source_python and install_locked(source_python, [RUNTIME_LOCK], report, stage,
                                        "install README quick-start profile (runtime.lock)"):
        env = profile_env(workdir, "profile-source")
        for label, arguments, expected in CLI_COMMANDS:
            result = run([str(source_python), "-m", "poptrap", *arguments], cwd=ROOT, env=env, timeout=120)
            report.check(stage, f"source tree · python -m poptrap {' '.join(arguments)} · {label}",
                         result.returncode == expected,
                         f"exit {result.returncode}" if result.returncode == expected
                         else f"expected exit {expected}, got {result.returncode}: "
                              f"{(result.stderr or result.stdout).strip()[-200:]}")
        config_file = Path(env["POPTRAP_CONFIG_DIR"]) / "config.toml"
        private = config_file.is_file() and (
            (config_file.stat().st_mode & 0o777) == 0o600 if os.name == "posix" else True
        )
        report.check(stage, "source tree · --init-config wrote a private user file", private, str(config_file))

    # 2. Documented wheel profile: locked dependencies, then the wheel with --no-deps.
    installed_python = create_venv(workdir / "installed-venv", python, report, stage)
    if installed_python is None:
        return
    if not install_locked(installed_python, [bundle / "runtime.lock"], report, stage,
                          "install bundled runtime.lock"):
        return
    result = run([str(installed_python), "-m", "pip", "install", "--no-deps", str(wheel)], timeout=600)
    if not report.check(stage, "install the built wheel with --no-deps", result.returncode == 0,
                        (result.stderr or result.stdout).strip().splitlines()[-1][:200] if result.returncode else wheel.name):
        return
    result = run([str(installed_python), "-m", "pip", "check"], timeout=300)
    report.check(stage, "pip check reports no broken requirements",
                 result.returncode == 0, (result.stdout or result.stderr).strip().splitlines()[0][:200])

    launcher = installed_python.parent / ("poptrap.exe" if os.name == "nt" else "poptrap")
    env = profile_env(workdir, "profile-installed")
    outside = workdir  # deliberately outside the checkout
    for label, arguments, expected in CLI_COMMANDS:
        result = run([str(launcher), *arguments], cwd=outside, env=env, timeout=120)
        report.check(stage, f"installed · poptrap {' '.join(arguments)} · {label}",
                     result.returncode == expected,
                     f"exit {result.returncode}" if result.returncode == expected
                     else f"expected exit {expected}, got {result.returncode}: "
                          f"{(result.stderr or result.stdout).strip()[-200:]}")
    result = run([str(installed_python), "-m", "poptrap", "--version"], cwd=outside, env=env, timeout=120)
    report.check(stage, "installed · python -m poptrap --version", result.returncode == 0,
                 result.stdout.strip()[:80])

    result = run([str(installed_python), "-I", str(ROOT / "tools" / "check_install.py")],
                 cwd=outside, env=env, timeout=900)
    report.check(stage, "installed smoke test (metadata, PTY launch, listeners, teardown)",
                 result.returncode == 0,
                 result.stdout.strip().splitlines()[-1][:200] if result.returncode == 0
                 else (result.stderr or result.stdout).strip()[-300:])

    # 3. Documented uninstall behavior: package files go, user data stays.
    kept_config = Path(env["POPTRAP_CONFIG_DIR"]) / "config.toml"
    result = run([str(installed_python), "-m", "pip", "uninstall", "-y", "poptrap"], timeout=300)
    report.check(stage, "pip uninstall poptrap", result.returncode == 0,
                 (result.stdout or result.stderr).strip().splitlines()[-1][:200] if result.returncode else "removed")
    report.check(stage, "console script is removed by the uninstall", not launcher.exists(), str(launcher))
    gone = run([str(installed_python), "-m", "poptrap", "--version"], cwd=outside, env=env, timeout=60)
    report.check(stage, "python -m poptrap no longer resolves after uninstall", gone.returncode != 0,
                 f"exit {gone.returncode}")
    report.check(stage, "user configuration survives uninstall", kept_config.is_file(), str(kept_config))


def main(argv: list[str] | None = None) -> int:
    """Run the selected verification stages and return a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stages", default="structure,hashes,platforms,fresh",
                        help="Comma-separated subset of: structure, hashes, platforms, fresh.")
    parser.add_argument("--workdir", type=Path, default=None,
                        help="Work directory (default: a temporary directory that is removed).")
    parser.add_argument("--python", default=sys.executable,
                        help="Interpreter used to create the clean virtual environments.")
    parser.add_argument("--epoch", type=int, default=1794960000,
                        help="SOURCE_DATE_EPOCH for the reproducible build (default: the documented RC epoch).")
    parser.add_argument("--keep", action="store_true", help="Keep the work directory for inspection.")
    args = parser.parse_args(argv)

    selected = [name.strip() for name in args.stages.split(",") if name.strip()]
    unknown = [name for name in selected if name not in {"structure", "hashes", "platforms", "fresh"}]
    if unknown:
        parser.error(f"Unknown stage(s): {', '.join(unknown)}")
    if not selected:
        parser.error("Select at least one stage.")

    networked = [name for name in selected if name in {"hashes", "platforms", "fresh"}]
    if networked:
        try:
            fetch_pypi("pip", "23.0.1", timeout=20)
        except Exception as exc:  # noqa: BLE001
            print(f"Network stages {networked} need access to pypi.org: {exc}", file=sys.stderr)
            return 2

    report = Report()
    print(f"POPTRAP installation verification · repo {ROOT} · python {args.python}")
    print(f"Stages: {', '.join(selected)}\n", flush=True)

    temporary = None
    workdir = args.workdir
    if workdir is None:
        temporary = tempfile.mkdtemp(prefix="poptrap-verify-install-")
        workdir = Path(temporary)
    workdir.mkdir(parents=True, exist_ok=True)

    def run_stage(name: str, action: Callable[[], None]) -> None:
        """Execute one stage, converting an internal error into a reported failure."""
        try:
            action()
        except Exception as exc:  # noqa: BLE001 - a verification run must always report
            report.record(name, "stage ran without an internal error", "FAIL", f"{type(exc).__name__}: {exc}")

    try:
        if "structure" in selected:
            run_stage("structure", lambda: stage_structure(report))
        if "hashes" in selected:
            run_stage("hashes", lambda: stage_hashes(report))
        if "platforms" in selected:
            run_stage("platforms", lambda: stage_platforms(report, workdir, args.python))
        if "fresh" in selected:
            run_stage("fresh", lambda: stage_fresh(report, workdir, args.python, args.epoch))
    finally:
        report.summarize()
        if temporary and not args.keep:
            shutil.rmtree(temporary, ignore_errors=True)
        elif args.keep:
            print(f"Work directory kept at {workdir}")

    return 1 if report.failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
