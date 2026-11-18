"""Phase 5 automated tests for Bluetooth capability detection and platform adapters.

NOTE ON TEST SCOPE:
- ``TestMocked*`` classes use controlled dependency-injected mocks/fixtures and
  do NOT claim physical Bluetooth radio testing.
- ``TestHostEnvironmentCapabilityInspection`` exercises real, unmocked host
  capability probing on the current machine without requiring or claiming
  attached Bluetooth hardware.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from poptrap.engine.bluetooth import (
    AdapterManager,
    AndroidTermuxBackend,
    BluetoothApiKind,
    BluetoothDisabledError,
    BluetoothEngine,
    BluetoothError,
    BluetoothPermissionError,
    BluetoothUnavailableError,
    BluetoothUnsupportedError,
    CapabilityDetector,
    CapabilityState,
    CommandResult,
    LinuxBluetoothBackend,
    MacOSBluetoothBackend,
    TransportKind,
    UnsupportedPlatformBackend,
    WindowsBluetoothBackend,
    create_platform_backend,
    mask_adapter_address,
)
from poptrap.utils.platform import OperatingSystem, PlatformInfo, detect_platform


def _make_platform(
    os_type: OperatingSystem,
    *,
    system_name: str,
    is_wsl: bool = False,
    is_termux: bool = False,
) -> PlatformInfo:
    return PlatformInfo(
        os_type=os_type,
        system_name=system_name,
        release="6.6.0",
        version="6.6.0",
        architecture="x86_64",
        python_version="3.11.2",
        python_version_tuple=(3, 11, 2),
        is_termux=is_termux,
        is_wsl=is_wsl,
    )


def _create_fake_sysfs_adapter(
    tmp_path: Path,
    *,
    name: str = "hci0",
    address: str = "AA:BB:CC:DD:12:34",
    rfkill_soft: str | None = None,
    rfkill_hard: str = "0",
) -> tuple[Path, Path, Path]:
    sys_bt = tmp_path / "sys_bt"
    hci = sys_bt / name
    hci.mkdir(parents=True)
    (hci / "address").write_text(f"{address}\n", encoding="utf-8")
    (hci / "name").write_text("Mock USB Controller\n", encoding="utf-8")

    sys_rfkill = tmp_path / "sys_rfkill"
    if rfkill_soft is not None:
        rk = sys_rfkill / "rfkill0"
        rk.mkdir(parents=True)
        (rk / "type").write_text("bluetooth\n", encoding="utf-8")
        (rk / "soft").write_text(f"{rfkill_soft}\n", encoding="utf-8")
        (rk / "hard").write_text(f"{rfkill_hard}\n", encoding="utf-8")

    dbus_sock = tmp_path / "system_bus_socket"
    dbus_sock.write_text("", encoding="utf-8")
    return sys_bt, sys_rfkill, dbus_sock


BLUEZ_SHOW_POWERED = """\
Controller AA:BB:CC:DD:12:34 (public)
\tName: Workstation-BT
\tAlias: Workstation-BT
\tPowered: yes
\tDiscoverable: no
\tPairable: yes
"""

BLUEZ_SHOW_OFF = """\
Controller AA:BB:CC:DD:12:34 (public)
\tName: Workstation-BT
\tPowered: no
\tDiscoverable: no
\tPairable: yes
"""


class TestMockedLinuxCapabilityDetection:
    """Controlled mocked tests for Linux/BlueZ capability states."""

    def test_supported_powered_linux_adapter_reports_available(
        self, tmp_path: Path
    ) -> None:
        sys_bt, sys_rfkill, dbus_sock = _create_fake_sysfs_adapter(
            tmp_path, rfkill_soft="0"
        )
        backend = LinuxBluetoothBackend(
            sys_bluetooth_dir=sys_bt,
            sys_rfkill_dir=sys_rfkill,
            dbus_sockets=(dbus_sock,),
            which_fn=lambda cmd: "/usr/bin/bluetoothctl" if cmd == "bluetoothctl" else None,
            command_runner=lambda _argv, _t, _c: CommandResult(
                returncode=0, stdout=BLUEZ_SHOW_POWERED, stderr=""
            ),
            rfcomm_check=lambda: True,
            module_check=lambda _m: False,
        )
        pinfo = _make_platform(OperatingSystem.LINUX, system_name="Linux")
        report = backend.detect_capabilities(pinfo)

        assert report.overall_state is CapabilityState.AVAILABLE
        assert report.platform_support.state is CapabilityState.AVAILABLE
        assert report.hardware.state is CapabilityState.AVAILABLE
        assert report.adapter.state is CapabilityState.AVAILABLE
        assert report.adapter_powered.state is CapabilityState.AVAILABLE
        assert report.discovery.state is CapabilityState.AVAILABLE
        assert report.pairing.state is CapabilityState.AVAILABLE
        assert report.invitation_transport.state is CapabilityState.AVAILABLE
        assert report.can_discover is True
        assert report.can_pair is True
        assert report.can_send_invitation is True
        assert report.has_powered_adapter is True
        assert TransportKind.RFCOMM in report.supported_transports
        assert BluetoothApiKind.BLUEZ_BLUETOOTHCTL in report.supported_apis
        assert BluetoothApiKind.PYTHON_AF_BLUETOOTH in report.supported_apis
        assert len(report.adapters) == 1
        # Full local MAC is masked for privacy.
        assert report.adapters[0].address_masked == "XX:XX:XX:XX:12:34"
        assert "AA:BB:CC:DD" not in (report.adapters[0].address_masked or "")
        assert report.required_permissions

    def test_missing_linux_adapter_and_wsl_note(self, tmp_path: Path) -> None:
        backend = LinuxBluetoothBackend(
            sys_bluetooth_dir=tmp_path / "absent_bt",
            sys_rfkill_dir=tmp_path / "absent_rfkill",
            dbus_sockets=(tmp_path / "absent_dbus",),
            which_fn=lambda _cmd: None,
            rfcomm_check=lambda: True,
            module_check=lambda _m: False,
        )
        wsl_info = _make_platform(
            OperatingSystem.LINUX, system_name="Linux", is_wsl=True
        )
        report = backend.detect_capabilities(wsl_info)

        assert report.overall_state is CapabilityState.UNAVAILABLE
        assert report.hardware.state is CapabilityState.UNAVAILABLE
        assert report.adapter.state is CapabilityState.UNAVAILABLE
        assert report.adapter_powered.state is CapabilityState.UNAVAILABLE
        assert report.can_discover is False
        assert report.can_pair is False
        assert report.can_send_invitation is False
        assert report.adapters == ()
        assert any("WSL" in note for note in report.notes)
        assert report.alternative_workflow is not None
        assert report.alternative_workflow.uses_bluetooth is False

    @pytest.mark.parametrize(
        ("bt_output", "rfkill_soft"),
        [
            (BLUEZ_SHOW_OFF, "0"),
            (BLUEZ_SHOW_POWERED, "1"),
        ],
    )
    def test_disabled_or_rfkill_blocked_adapter_reports_disabled(
        self, tmp_path: Path, bt_output: str, rfkill_soft: str
    ) -> None:
        sys_bt, sys_rfkill, dbus_sock = _create_fake_sysfs_adapter(
            tmp_path, rfkill_soft=rfkill_soft
        )
        backend = LinuxBluetoothBackend(
            sys_bluetooth_dir=sys_bt,
            sys_rfkill_dir=sys_rfkill,
            dbus_sockets=(dbus_sock,),
            which_fn=lambda cmd: "/usr/bin/bluetoothctl" if cmd == "bluetoothctl" else None,
            command_runner=lambda _argv, _t, _c: CommandResult(
                returncode=0, stdout=bt_output, stderr=""
            ),
            rfcomm_check=lambda: True,
            module_check=lambda _m: False,
        )
        detector = CapabilityDetector(
            backend, _make_platform(OperatingSystem.LINUX, system_name="Linux")
        )
        report = detector.detect()

        assert report.overall_state is CapabilityState.DISABLED
        assert report.hardware.state is CapabilityState.AVAILABLE
        assert report.adapter_powered.state is CapabilityState.DISABLED
        assert report.discovery.state is CapabilityState.DISABLED
        assert report.pairing.state is CapabilityState.DISABLED
        assert report.invitation_transport.state is CapabilityState.DISABLED
        assert report.alternative_workflow is not None
        assert report.alternative_workflow.uses_bluetooth is False

        adapter_mgr = AdapterManager(detector)
        assert adapter_mgr.is_powered_and_ready() is False
        with pytest.raises(BluetoothDisabledError):
            adapter_mgr.require_ready_adapter()

    def test_dbus_permission_denied_reports_permission_required(
        self, tmp_path: Path
    ) -> None:
        sys_bt, sys_rfkill, dbus_sock = _create_fake_sysfs_adapter(tmp_path)
        backend = LinuxBluetoothBackend(
            sys_bluetooth_dir=sys_bt,
            sys_rfkill_dir=sys_rfkill,
            dbus_sockets=(dbus_sock,),
            which_fn=lambda cmd: "/usr/bin/bluetoothctl" if cmd == "bluetoothctl" else None,
            command_runner=lambda _argv, _t, _c: CommandResult(
                returncode=1,
                stdout="",
                stderr="org.freedesktop.DBus.Error.AccessDenied: Rejected send message",
            ),
            rfcomm_check=lambda: True,
            module_check=lambda _m: False,
        )
        detector = CapabilityDetector(
            backend, _make_platform(OperatingSystem.LINUX, system_name="Linux")
        )
        report = detector.detect()

        assert report.overall_state is CapabilityState.PERMISSION_REQUIRED
        assert report.adapter.state is CapabilityState.PERMISSION_REQUIRED
        assert report.discovery.state is CapabilityState.PERMISSION_REQUIRED
        assert report.pairing.state is CapabilityState.PERMISSION_REQUIRED
        assert report.invitation_transport.state is CapabilityState.PERMISSION_REQUIRED

        with pytest.raises(BluetoothPermissionError):
            AdapterManager(detector).require_ready_adapter()

    def test_daemon_timeout_and_unexpected_backend_exception_report_error(
        self, tmp_path: Path
    ) -> None:
        dbus_sock = tmp_path / "dbus"
        dbus_sock.write_text("", encoding="utf-8")
        timeout_backend = LinuxBluetoothBackend(
            sys_bluetooth_dir=tmp_path / "no_bt",
            sys_rfkill_dir=tmp_path / "no_rk",
            dbus_sockets=(dbus_sock,),
            which_fn=lambda cmd: "/usr/bin/bluetoothctl" if cmd == "bluetoothctl" else None,
            command_runner=lambda _argv, _t, _c: CommandResult(
                returncode=-1, stdout="", stderr="", timed_out=True
            ),
            rfcomm_check=lambda: True,
            module_check=lambda _m: False,
        )
        pinfo = _make_platform(OperatingSystem.LINUX, system_name="Linux")
        r1 = timeout_backend.detect_capabilities(pinfo)
        assert r1.overall_state is CapabilityState.ERROR
        assert r1.discovery.state is CapabilityState.ERROR

        class _ExplodingBackend(UnsupportedPlatformBackend):
            def detect_capabilities(self, platform_info: PlatformInfo):  # type: ignore[override]
                raise RuntimeError("synthetic bus crash")

        detector = CapabilityDetector(_ExplodingBackend(), pinfo)
        r2 = detector.detect()
        assert r2.overall_state is CapabilityState.ERROR
        assert "synthetic bus crash" in r2.hardware.reason
        with pytest.raises(BluetoothError):
            AdapterManager(detector).require_ready_adapter()


class TestMockedCrossPlatformAdapters:
    """Controlled mocked tests for Windows, macOS, Android Termux, and unknown OS."""

    def test_windows_backend_active_disabled_missing_and_pairing_unsupported(self) -> None:
        pinfo = _make_platform(OperatingSystem.WINDOWS, system_name="Windows")

        ok_json = json.dumps(
            [
                {
                    "FriendlyName": "Intel(R) Wireless Bluetooth(R)",
                    "Status": "OK",
                    "InstanceId": "USB\\VID_8087&PID_0032",
                    "ConfigManagerErrorCode": 0,
                }
            ]
        )
        win_ok = WindowsBluetoothBackend(
            which_fn=lambda cmd: "powershell.exe" if cmd == "powershell" else None,
            command_runner=lambda _a, _t, _c: CommandResult(0, ok_json, ""),
            rfcomm_check=lambda: True,
            module_check=lambda mod: mod == "bleak",
        )
        rep = win_ok.detect_capabilities(pinfo)
        assert rep.hardware.state is CapabilityState.AVAILABLE
        assert rep.adapter_powered.state is CapabilityState.AVAILABLE
        assert rep.discovery.state is CapabilityState.AVAILABLE
        assert rep.pairing.state is CapabilityState.UNSUPPORTED
        assert rep.invitation_transport.state is CapabilityState.AVAILABLE
        with pytest.raises(BluetoothUnsupportedError):
            win_ok.pair_device("AA:BB:CC:DD:EE:FF", timeout_seconds=5.0, cancel_event=threading.Event())

        disabled_json = json.dumps(
            {
                "FriendlyName": "Intel(R) Wireless Bluetooth(R)",
                "Status": "Error",
                "InstanceId": "USB\\VID_8087",
                "ConfigManagerErrorCode": 22,
            }
        )
        win_off = WindowsBluetoothBackend(
            which_fn=lambda cmd: "powershell.exe" if cmd == "powershell" else None,
            command_runner=lambda _a, _t, _c: CommandResult(0, disabled_json, ""),
            rfcomm_check=lambda: True,
            module_check=lambda _m: False,
        )
        rep_off = win_off.detect_capabilities(pinfo)
        assert rep_off.overall_state is CapabilityState.DISABLED
        assert rep_off.adapter_powered.state is CapabilityState.DISABLED

        win_none = WindowsBluetoothBackend(
            which_fn=lambda cmd: "powershell.exe" if cmd == "powershell" else None,
            command_runner=lambda _a, _t, _c: CommandResult(
                1, "", "Get-PnpDevice: No PnP devices found"
            ),
            rfcomm_check=lambda: True,
            module_check=lambda _m: False,
        )
        rep_none = win_none.detect_capabilities(pinfo)
        assert rep_none.overall_state is CapabilityState.UNAVAILABLE
        assert rep_none.adapters == ()

        win_bad = WindowsBluetoothBackend(
            which_fn=lambda cmd: "powershell.exe" if cmd == "powershell" else None,
            command_runner=lambda _a, _t, _c: CommandResult(0, "{not-json", ""),
            rfcomm_check=lambda: False,
            module_check=lambda _m: False,
        )
        assert win_bad.detect_capabilities(pinfo).overall_state is CapabilityState.ERROR

    def test_macos_backend_on_off_permission_and_honest_transport_limits(self) -> None:
        pinfo = _make_platform(OperatingSystem.MACOS, system_name="macOS")
        on_payload = json.dumps(
            {
                "SPBluetoothDataType": [
                    {
                        "controller_properties": {
                            "controller_address": "10:20:30:40:50:60",
                            "controller_chipset": "BCM_4387",
                            "controller_state": "attrib_on",
                            "controller_discoverable": "attrib_off",
                        }
                    }
                ]
            }
        )
        mac_on = MacOSBluetoothBackend(
            which_fn=lambda cmd: "/usr/sbin/system_profiler" if cmd == "system_profiler" else None,
            command_runner=lambda _a, _t, _c: CommandResult(0, on_payload, ""),
            module_check=lambda mod: mod == "bleak",
        )
        rep = mac_on.detect_capabilities(pinfo)
        assert rep.hardware.state is CapabilityState.AVAILABLE
        assert rep.adapter_powered.state is CapabilityState.AVAILABLE
        assert rep.discovery.state is CapabilityState.AVAILABLE
        assert rep.pairing.state is CapabilityState.UNSUPPORTED
        assert rep.invitation_transport.state is CapabilityState.UNSUPPORTED
        assert rep.adapters[0].address_masked == "XX:XX:XX:XX:50:60"
        assert rep.alternative_workflow is not None
        assert rep.alternative_workflow.uses_bluetooth is False

        off_payload = json.dumps(
            {
                "SPBluetoothDataType": [
                    {"controller_properties": {"controller_state": "attrib_off"}}
                ]
            }
        )
        mac_off = MacOSBluetoothBackend(
            which_fn=lambda _c: "/usr/sbin/system_profiler",
            command_runner=lambda _a, _t, _c: CommandResult(0, off_payload, ""),
            module_check=lambda _m: False,
        )
        assert mac_off.detect_capabilities(pinfo).overall_state is CapabilityState.DISABLED

        mac_perm = MacOSBluetoothBackend(
            which_fn=lambda _c: "/usr/sbin/system_profiler",
            command_runner=lambda _a, _t, _c: CommandResult(
                1, "", "TCC access denied for Bluetooth"
            ),
            module_check=lambda _m: False,
        )
        assert (
            mac_perm.detect_capabilities(pinfo).overall_state
            is CapabilityState.PERMISSION_REQUIRED
        )

    def test_android_termux_and_unknown_os_report_unsupported_honestly(self) -> None:
        termux_info = _make_platform(
            OperatingSystem.TERMUX, system_name="Termux", is_termux=True
        )
        android = AndroidTermuxBackend(which_fn=lambda _c: "/data/data/com.termux/files/usr/bin/termux-info")
        t_rep = android.detect_capabilities(termux_info)
        assert t_rep.overall_state is CapabilityState.UNSUPPORTED
        assert t_rep.can_discover is False
        assert t_rep.can_pair is False
        assert t_rep.can_send_invitation is False
        assert BluetoothApiKind.ANDROID_TERMUX_API in t_rep.supported_apis
        assert any("BLUETOOTH_SCAN" in p for p in t_rep.required_permissions)
        assert t_rep.alternative_workflow is not None
        assert t_rep.alternative_workflow.uses_bluetooth is False
        with pytest.raises(BluetoothUnsupportedError):
            android.scan_devices(timeout_seconds=4.0, cancel_event=threading.Event())
        with pytest.raises(BluetoothUnsupportedError):
            android.pair_device("AA:BB:CC:DD:EE:FF", timeout_seconds=4.0, cancel_event=threading.Event())

        unknown_info = _make_platform(OperatingSystem.UNKNOWN, system_name="FreeBSD")
        unknown_backend = create_platform_backend(unknown_info)
        assert isinstance(unknown_backend, UnsupportedPlatformBackend)
        u_rep = unknown_backend.detect_capabilities(unknown_info)
        assert u_rep.overall_state is CapabilityState.UNSUPPORTED
        assert u_rep.platform_support.state is CapabilityState.UNSUPPORTED
        assert u_rep.discovery.state is CapabilityState.UNSUPPORTED
        assert u_rep.pairing.state is CapabilityState.UNSUPPORTED
        assert u_rep.invitation_transport.state is CapabilityState.UNSUPPORTED
        assert u_rep.alternative_workflow is not None
        assert u_rep.alternative_workflow.uses_bluetooth is False

    def test_platform_factory_and_mac_masking(self) -> None:
        assert isinstance(
            create_platform_backend(_make_platform(OperatingSystem.LINUX, system_name="Linux")),
            LinuxBluetoothBackend,
        )
        assert isinstance(
            create_platform_backend(_make_platform(OperatingSystem.WINDOWS, system_name="Windows")),
            WindowsBluetoothBackend,
        )
        assert isinstance(
            create_platform_backend(_make_platform(OperatingSystem.MACOS, system_name="macOS")),
            MacOSBluetoothBackend,
        )
        assert isinstance(
            create_platform_backend(
                _make_platform(OperatingSystem.TERMUX, system_name="Termux", is_termux=True)
            ),
            AndroidTermuxBackend,
        )
        assert mask_adapter_address("11:22:33:44:55:ab") == "XX:XX:XX:XX:55:AB"
        assert mask_adapter_address("00:00:00:00:00:00") is None
        assert mask_adapter_address("not-a-mac") is None


class TestHostEnvironmentCapabilityInspection:
    """Unmocked capability check against the current sandbox host environment."""

    def test_real_host_capability_check_never_fakes_connection_or_crashes(self) -> None:
        engine = BluetoothEngine(platform_info=detect_platform())
        try:
            report = engine.check_capabilities()
            assert report.overall_state in set(CapabilityState)
            conn = engine.connection_status()
            assert conn.confirmed is False
            if not report.can_discover:
                with pytest.raises((BluetoothUnavailableError, BluetoothUnsupportedError, BluetoothDisabledError, BluetoothPermissionError)):
                    engine.discover_devices(user_initiated=True, timeout_seconds=3.0)
        finally:
            engine.shutdown()
