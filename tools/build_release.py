"""Build and verify local POPTRAP archives with the hash-locked setuptools backend.

No credentials, network access, upload step, or Git metadata are needed. The
wheel is built from the generated source distribution, not the checkout.
"""

from __future__ import annotations

import argparse
import base64
import csv
import gzip
import hashlib
import io
import os
import runpy
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from email import message_from_bytes
from fnmatch import fnmatchcase
from importlib.metadata import version as installed_version
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = (
    "pyproject.toml", "MANIFEST.in", "poptrap.py", "LICENSE", "README.md", "CHANGELOG.md",
    "THIRD_PARTY_NOTICES.md", "SECURITY.md", "CONTRIBUTING.md", "CODE_OF_CONDUCT.md", "requirements.txt",
)
INCLUDE = {
    "poptrap": ("*.py", "*.toml", "*.tcss", "*.html", "*.css", "*.js", "*.txt", "py.typed"),
    "docs": ("*.md", "*.png"),
    "requirements": ("*.lock",),
    "tests": ("*.py", "*.mjs"),
    "tools": ("*.py",),
}
REQUIRED_PACKAGE_MEMBERS = {
    "poptrap/data/default.toml", "poptrap/py.typed", "poptrap/ui/theme.tcss", "poptrap/ui/styles/control.tcss",
    "poptrap/web/index.html", "poptrap/web/studio.html", "poptrap/web/bluetooth.html", "poptrap/web/control.html",
    "poptrap/web/delivery.html", "poptrap/web/scan.html", "poptrap/web/scripts/document-key.js",
    "poptrap/web/scripts/vendor/jsQR.js", "poptrap/web/scripts/vendor/jsQR.LICENSE.txt",
}


def stage_source(destination: Path) -> set[str]:
    """Copy only intentional release inputs, never environment, Git or build state."""
    paths = [ROOT / name for name in ROOT_FILES]
    for folder, patterns in INCLUDE.items():
        paths.extend(path for path in (ROOT / folder).rglob("*") if any(fnmatchcase(path.name, p) for p in patterns))
    copied = set()
    for path in sorted(paths):
        relative = path.relative_to(ROOT)
        if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
            continue
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(ROOT):
            raise ValueError(f"Release input must be a regular, in-tree file: {relative}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        target.chmod(0o755 if relative.as_posix() == "poptrap.py" else 0o644)
        copied.add(relative.as_posix())
    return copied


def run_backend(source: Path, method: str, output: Path, epoch: int) -> None:
    subprocess.run(
        # The backend propagates its umask into wheel entries. Set it only in
        # the private build subprocess, not in the caller or output directory.
        [sys.executable, "-I", "-c", "import os, sys; os.umask(0o022); from setuptools import build_meta; "
         "getattr(build_meta, sys.argv[1])(sys.argv[2])", method, str(output)],
        cwd=source, env={**os.environ, "SOURCE_DATE_EPOCH": str(epoch)}, check=True,
    )


def safe_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or ".." in path.parts or "\\" in name or ":" in name:
        raise ValueError(f"Unexpected archive path: {name!r}")
    return path


def check_metadata(raw: bytes, version: str) -> None:
    metadata = message_from_bytes(raw)
    if (metadata["Name"], metadata["Version"], metadata["Requires-Python"], metadata["License-Expression"]) != (
        "poptrap", version, ">=3.11", "MIT AND Apache-2.0",
    ):
        raise ValueError("Distribution metadata does not match the application identity and license.")


def normalize_sdist(raw: Path, output: Path, unpack_to: Path, expected: set[str], epoch: int, version: str) -> Path:
    """Normalize tar metadata and extract only validated regular build inputs."""
    prefix = f"poptrap-{version}"
    generated = {"PKG-INFO", "setup.cfg", "poptrap.egg-info/PKG-INFO", "poptrap.egg-info/SOURCES.txt",
                 "poptrap.egg-info/dependency_links.txt", "poptrap.egg-info/top_level.txt",
                 "poptrap.egg-info/entry_points.txt", "poptrap.egg-info/requires.txt"}
    seen: set[str] = set()
    with tarfile.open(raw, "r:gz") as source, output.open("wb") as stream:
        with gzip.GzipFile(filename="", fileobj=stream, mtime=epoch) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for member in sorted(source.getmembers(), key=lambda item: item.name):
                    path = safe_name(member.name)
                    if path.parts[0] != prefix or not (member.isfile() or member.isdir()):
                        raise ValueError(f"Unexpected source archive member: {member.name!r}")
                    name = PurePosixPath(*path.parts[1:]).as_posix()
                    entry = tarfile.TarInfo(path.as_posix())
                    entry.mtime = epoch
                    if member.isdir():
                        entry.type, entry.mode = tarfile.DIRTYPE, 0o755
                        archive.addfile(entry)
                        continue
                    if name in seen or name not in expected | generated:
                        raise ValueError(f"Unexpected or duplicate source file: {name!r}")
                    seen.add(name)
                    data = source.extractfile(member)
                    if data is None:
                        raise ValueError(f"Could not read {name!r}")
                    content = data.read()
                    if name == "PKG-INFO":
                        check_metadata(content, version)
                    entry.mode, entry.size = (0o755 if name == "poptrap.py" else 0o644), len(content)
                    archive.addfile(entry, io.BytesIO(content))
                    target = unpack_to.joinpath(*path.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                    target.chmod(entry.mode)
    if not (expected | {"PKG-INFO"}) <= seen:
        missing = (expected | {"PKG-INFO"}) - seen
        raise ValueError(f"Files missing from the source distribution: {sorted(missing)}")
    return unpack_to / prefix


def verify_wheel(wheel: Path, source: Path, source_paths: set[str], version: str) -> None:
    prefix = f"poptrap-{version}.dist-info/"
    members = {path for path in source_paths if path.startswith("poptrap/")}
    required_metadata = {prefix + name for name in (
        "METADATA", "WHEEL", "entry_points.txt", "RECORD", "licenses/LICENSE", "licenses/THIRD_PARTY_NOTICES.md",
        "licenses/poptrap/web/scripts/vendor/jsQR.LICENSE.txt",
    )}
    allowed = members | required_metadata | {prefix + "top_level.txt"}
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) - allowed or (members | required_metadata) - set(names):
            raise ValueError("Wheel contains unexpected, missing or duplicate files.")
        if not REQUIRED_PACKAGE_MEMBERS <= members:
            raise ValueError("Required bundled defaults or web/terminal assets are missing.")
        record_name = prefix + "RECORD"
        rows = list(csv.reader(io.StringIO(archive.read(record_name).decode("utf-8"))))
        record = {name: (digest, size) for name, digest, size in rows}
        if len(rows) != len(names) or set(record) != set(names) or record[record_name] != ("", ""):
            raise ValueError("Wheel RECORD does not cover precisely the archive entries.")
        for member in archive.infolist():
            name = member.filename
            if safe_name(name).as_posix() != name or member.is_dir():
                raise ValueError(f"Unexpected wheel path: {name!r}")
            mode = member.external_attr >> 16
            if stat.S_IFMT(mode) not in (0, stat.S_IFREG) or mode & 0o7111:
                raise ValueError(f"Unexpected wheel file type or executable mode: {name!r}")
            content = archive.read(member)
            if name != record_name:
                digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode("ascii")
                if record[name] != (f"sha256={digest}", str(len(content))):
                    raise ValueError(f"Wheel RECORD digest or size mismatch: {name!r}")
            if name in members and content != (source / name).read_bytes():
                raise ValueError(f"Wheel content differs from the sdist: {name!r}")
        check_metadata(archive.read(prefix + "METADATA"), version)
        if b"poptrap = poptrap.cli:main" not in archive.read(prefix + "entry_points.txt"):
            raise ValueError("The wheel did not define the POPTRAP console script.")
        if b"Tag: py3-none-any" not in archive.read(prefix + "WHEEL"):
            raise ValueError("Expected a platform-independent Python wheel.")


def build(output: Path, epoch: int, version: str) -> None:
    expected_backend = next(line.split("==", 1)[1].split()[0] for line in (
        ROOT / "requirements/build.lock"
    ).read_text(encoding="utf-8").splitlines() if line.startswith("setuptools=="))
    if installed_version("setuptools") != expected_backend:
        raise ValueError(f"Install requirements/build.lock first (setuptools=={expected_backend}).")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("Choose a new or empty output directory; existing files will not be overwritten.")
    with tempfile.TemporaryDirectory(prefix="poptrap-release-") as tmp:
        work = Path(tmp)
        staged, raw, results = (work / directory for directory in ("source", "raw", "artifacts"))
        for directory in (staged, raw, results):
            directory.mkdir()
        source_paths = stage_source(staged)
        run_backend(staged, "build_sdist", raw, epoch)
        sdist_name = f"poptrap-{version}.tar.gz"
        source = normalize_sdist(raw / sdist_name, results / sdist_name, work / "unpacked", source_paths, epoch, version)
        run_backend(source, "build_wheel", results, epoch)
        wheel = results / f"poptrap-{version}-py3-none-any.whl"
        verify_wheel(wheel, source, source_paths, version)
        shutil.copyfile(staged / "requirements/runtime.lock", results / "runtime.lock")
        sums = []
        output.mkdir(parents=True, exist_ok=True)
        for file in sorted(results.iterdir()):
            with file.open("rb") as stream:
                sums.append(f"{hashlib.file_digest(stream, 'sha256').hexdigest()}  {file.name}\n")
            with file.open("rb") as stream, (output / file.name).open("xb") as target:
                shutil.copyfileobj(stream, target)
        with (output / "SHA256SUMS").open("x", encoding="ascii", newline="\n") as stream:
            stream.writelines(sums)
    print(f"Verified archives and SHA256SUMS written to {output}")


def main() -> None:
    version = runpy.run_path(str(ROOT / "poptrap/_version.py"))["__version__"]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "release-artifacts" / version)
    parser.add_argument("--epoch", type=int, default=os.environ.get("SOURCE_DATE_EPOCH"),
                        help="Reproducible build timestamp (or set SOURCE_DATE_EPOCH).")
    args = parser.parse_args()
    if args.epoch is None or not 315532800 <= args.epoch <= 4294967295:
        parser.error("Provide a valid --epoch or SOURCE_DATE_EPOCH (1980 or later).")
    try:
        build(args.output.resolve(), args.epoch, version)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Release build failed: {exc}\n")


if __name__ == "__main__":
    main()
