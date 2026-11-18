"""Abstract platform interface and safe subprocess utilities for Bluetooth backends."""

from __future__ import annotations

import importlib.util
import re
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from poptrap.engine.bluetooth.connection import (
    BluetoothTransport,
    UnsupportedBluetoothTransport,
)
from poptrap.engine.bluetooth.models import (
    AdapterInfo,
    AlternativeWorkflow,
    CapabilityCheck,
    CapabilityReport,
    CapabilityState,
    DiscoveredDevice,
    PairingState,
)
from poptrap.utils.platform import PlatformInfo

_PERMISSION_PATTERNS = re.compile(
    r"(access\s*denied|permission\s*denied|not\s*authorized|operation\s*not\s*permitted|"
    r"org\.freedesktop\.dbus\.error\.accessdenied|org\.bluez\.error\.notauthorized|"
    r"rejected\s+send\s+message|tcc|unauthorizedaccess)",
    re.IGNORECASE,
)

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences emitted by CLI tools such as ``bluetoothctl``."""
    return _ANSI_ESCAPE_RE.sub("", text)


def looks_like_permission_error(text: str) -> bool:
    """Return True when tool output indicates an OS or bus permission denial."""
    return bool(text and _PERMISSION_PATTERNS.search(text))


def is_module_available(module_name: str) -> bool:
    """Safely check whether an optional Python module (e.g. ``bleak``) is installed."""
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError):
        return False


def default_alternative_workflow() -> AlternativeWorkflow:
    """Return the standard clearly labeled non-Bluetooth localhost alternative."""
    return AlternativeWorkflow()


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Captured outcome of a non-shell OS utility invocation."""

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    cancelled: bool = False
    error: str | None = None

    @property
    def combined_output(self) -> str:
        parts = [strip_ansi(self.stdout).strip(), strip_ansi(self.stderr).strip()]
        if self.error:
            parts.append(self.error.strip())
        return "\n".join(p for p in parts if p)


CommandRunner = Callable[[Sequence[str], float, threading.Event | None], CommandResult]


def run_safe_command(
    argv: Sequence[str],
    timeout: float,
    cancel_event: threading.Event | None = None,
) -> CommandResult:
    """Run an OS utility without a shell, supporting bounded timeout and cancellation.

    Guarantees child-process termination and pipe closure on timeout,
    cancellation, or exception.
    """
    args = [str(part) for part in argv]
    if not args or any("\x00" in part for part in args):
        return CommandResult(
            returncode=-1, stdout="", stderr="", error="Invalid command arguments."
        )

    bounded_timeout = max(0.5, float(timeout))
    if cancel_event is not None and cancel_event.is_set():
        return CommandResult(returncode=-1, stdout="", stderr="", cancelled=True)

    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
        deadline = time.monotonic() + bounded_timeout
        while True:
            if cancel_event is not None and cancel_event.is_set():
                _terminate_process(proc)
                out, err = _drain_process(proc)
                return CommandResult(
                    returncode=proc.returncode if proc.returncode is not None else -1,
                    stdout=out,
                    stderr=err,
                    cancelled=True,
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_process(proc)
                out, err = _drain_process(proc)
                return CommandResult(
                    returncode=proc.returncode if proc.returncode is not None else -1,
                    stdout=out,
                    stderr=err,
                    timed_out=True,
                )
            try:
                out, err = proc.communicate(timeout=min(0.1, remaining))
                return CommandResult(
                    returncode=proc.returncode if proc.returncode is not None else 0,
                    stdout=out,
                    stderr=err,
                )
            except subprocess.TimeoutExpired:
                continue
    except PermissionError as exc:
        return CommandResult(
            returncode=126,
            stdout="",
            stderr="",
            error=f"Permission denied executing '{args[0]}': {exc}",
        )
    except FileNotFoundError:
        return CommandResult(
            returncode=127,
            stdout="",
            stderr="",
            error=f"Required OS tool '{args[0]}' was not found.",
        )
    except OSError as exc:
        return CommandResult(
            returncode=-1,
            stdout="",
            stderr="",
            error=f"OS error running '{args[0]}': {exc}",
        )
    finally:
        if proc is not None and proc.poll() is None:
            _terminate_process(proc)
            _drain_process(proc)


def _terminate_process(proc: subprocess.Popen[str]) -> None:
    try:
        proc.terminate()
    except OSError:
        return
    try:
        proc.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass


def _drain_process(proc: subprocess.Popen[str]) -> tuple[str, str]:
    try:
        out, err = proc.communicate(timeout=1.0)
        return out or "", err or ""
    except Exception:  # noqa: BLE001
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        return "", ""


@dataclass(frozen=True, slots=True)
class RawDeviceObservation:
    """Raw device fields observed from an OS Bluetooth scan before normalization."""

    address: str
    name: str | None = None
    rssi: int | None = None
    paired: bool | None = None
    connected: bool | None = None
    address_type: str | None = None
    device_class: str | None = None
    transport: str | None = None
    adapter_id: str | None = None


@dataclass(frozen=True, slots=True)
class PairingOutcome:
    """Structured result from a platform pairing request."""

    state: PairingState
    detail: str
    used_os_dialog: bool = False


class BluetoothPlatformBackend(ABC):
    """Operating-system adapter contract for capability, scan, pair, and transport."""

    platform_name: str = "unknown"

    def __init__(
        self,
        *,
        wall_clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._wall_clock = (
            (lambda: datetime.now(UTC)) if wall_clock is None else wall_clock
        )

    def iso_now(self) -> str:
        return self._wall_clock().isoformat()

    @abstractmethod
    def detect_capabilities(self, platform_info: PlatformInfo) -> CapabilityReport:
        """Inspect host Bluetooth hardware, adapter state, permissions, and APIs."""

    @abstractmethod
    def list_adapters(self) -> tuple[AdapterInfo, ...]:
        """Return currently detected local Bluetooth adapters (empty if none)."""

    def has_powered_adapter(self, platform_info: PlatformInfo) -> bool:
        """Return True only when a local adapter is currently present and powered."""
        report = self.detect_capabilities(platform_info)
        return report.has_powered_adapter

    @abstractmethod
    def scan_devices(
        self,
        *,
        timeout_seconds: float,
        cancel_event: threading.Event,
    ) -> tuple[RawDeviceObservation, ...]:
        """Execute a single bounded, cancellable user-initiated device scan."""

    @abstractmethod
    def pair_device(
        self,
        address: str,
        *,
        timeout_seconds: float,
        cancel_event: threading.Event,
    ) -> PairingOutcome:
        """Initiate explicit OS pairing for ``address`` without bypassing auth."""

    def disconnect_device(self, address: str, *, timeout_seconds: float = 5.0) -> None:
        """Perform optional OS-level disconnect cleanup for ``address``."""
        del address, timeout_seconds

    def create_transport(self, report: CapabilityReport) -> BluetoothTransport:
        """Return a real ``BluetoothTransport`` for ``report`` or an unsupported stub."""
        return UnsupportedBluetoothTransport(report.invitation_transport.reason)

    def close(self) -> None:
        """Release any platform backend resources on shutdown."""
        return None


def compute_overall_state(checks: Sequence[CapabilityCheck]) -> CapabilityState:
    """Derive a concise overall capability state from individual checks."""
    states = [c.state for c in checks]
    if all(s is CapabilityState.AVAILABLE for s in states):
        return CapabilityState.AVAILABLE
    if CapabilityState.ERROR in states:
        return CapabilityState.ERROR
    if CapabilityState.PERMISSION_REQUIRED in states:
        return CapabilityState.PERMISSION_REQUIRED
    if CapabilityState.DISABLED in states:
        return CapabilityState.DISABLED
    if any(s is CapabilityState.AVAILABLE for s in states):
        # Partial support: return the most actionable blocker from functional ops.
        for candidate in (
            CapabilityState.UNAVAILABLE,
            CapabilityState.UNSUPPORTED,
        ):
            if candidate in states:
                return candidate
    if CapabilityState.UNSUPPORTED in states and CapabilityState.UNAVAILABLE not in states:
        return CapabilityState.UNSUPPORTED
    return CapabilityState.UNAVAILABLE


def run_bleak_discovery(
    *,
    timeout_seconds: float,
    cancel_event: threading.Event,
) -> tuple[RawDeviceObservation, ...]:
    """Optional real BLE scan via ``bleak`` when installed on host."""
    import asyncio
    from bleak import BleakScanner  # type: ignore[import-not-found]

    async def _scan() -> list[RawDeviceObservation]:
        discovered: dict[str, RawDeviceObservation] = {}

        def _callback(device: object, adv: object) -> None:
            addr = getattr(device, "address", None)
            if not isinstance(addr, str) or not addr.strip():
                return
            raw_name = getattr(adv, "local_name", None) or getattr(device, "name", None)
            clean_name = (
                raw_name.strip()
                if isinstance(raw_name, str) and raw_name.strip()
                else None
            )
            # Do not report an address echo as a device name.
            if clean_name and clean_name.upper().replace("-", ":") == addr.strip().upper():
                clean_name = None
            raw_rssi = getattr(adv, "rssi", None)
            rssi = int(raw_rssi) if isinstance(raw_rssi, int) and -140 <= raw_rssi <= 20 else None
            discovered[addr.strip().upper()] = RawDeviceObservation(
                address=addr.strip().upper(),
                name=clean_name,
                rssi=rssi,
                transport="le",
            )

        scanner = BleakScanner(detection_callback=_callback)
        await scanner.start()
        try:
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                if cancel_event.is_set():
                    break
                await asyncio.sleep(min(0.1, max(0.01, deadline - time.monotonic())))
        finally:
            await scanner.stop()
        return list(discovered.values())

    return tuple(asyncio.run(_scan()))
