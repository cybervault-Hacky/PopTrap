"""Phase 5 automated tests for Bluetooth discovery, pairing, connection, and cleanup.

All adapter/discovery/pairing interactions use controlled mocks and local UNIX
stream socket pairs (``socket.socketpair()``) to test protocol framing, ACK
verification, disconnect handling, and resource cleanup deterministically
without claiming physical Bluetooth radio hardware.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from poptrap.engine.bluetooth import (
    BluetoothAdapterDisconnectedError,
    BluetoothConnectionError,
    BluetoothConsentRequiredError,
    BluetoothDeviceNotFoundError,
    BluetoothDiscoveryError,
    BluetoothEngine,
    BluetoothPairingCancelledError,
    BluetoothPairingRejectedError,
    BluetoothTransportError,
    BluetoothUnsupportedError,
    CommandResult,
    ConnectionState,
    DiscoveryState,
    FRAME_MAGIC,
    LinuxBluetoothBackend,
    PairingState,
    RfcommSocketTransport,
    TransportKind,
    UnsupportedPlatformBackend,
    encode_ack_frame,
    normalize_device_address,
    recv_transport_frame,
    validate_discovery_timeout,
)
from poptrap.engine.bluetooth.models import MAX_INVITATION_BYTES
from poptrap.engine.bluetooth.platform.base import run_safe_command
from poptrap.utils.platform import OperatingSystem, PlatformInfo


def _linux_platform() -> PlatformInfo:
    return PlatformInfo(
        os_type=OperatingSystem.LINUX,
        system_name="Linux",
        release="6.6.0",
        version="6.6.0",
        architecture="x86_64",
        python_version="3.11.2",
        python_version_tuple=(3, 11, 2),
        is_termux=False,
        is_wsl=False,
    )


def _setup_sysfs(tmp_path: Path) -> tuple[Path, Path, Path]:
    sys_bt = tmp_path / "sys_bt"
    hci = sys_bt / "hci0"
    hci.mkdir(parents=True)
    (hci / "address").write_text("AA:BB:CC:00:11:22\n", encoding="utf-8")
    sys_rk = tmp_path / "sys_rk"
    dbus = tmp_path / "dbus.sock"
    dbus.write_text("", encoding="utf-8")
    return sys_bt, sys_rk, dbus


SHOW_ON = "Controller AA:BB:CC:00:11:22\n\tName: HostBT\n\tPowered: yes\n\tPairable: yes\n"


class _SocketPairRfcommTransport(RfcommSocketTransport):
    """Wraps a real OS ``socket.socketpair()`` end to exercise full framing/probing."""

    def __init__(self, client_sock: socket.socket) -> None:
        super().__init__()
        self._injected = client_sock

    def is_supported(self) -> tuple[bool, str]:
        return True, "Socketpair test transport"

    def connect(
        self,
        address: str,
        *,
        channel: int = 4,
        timeout: float = 5.0,
    ) -> None:
        normalize_device_address(address)
        self._injected.settimeout(timeout)
        with self._lock:
            self._sock = self._injected
            self._connected = True


class TestDeviceDiscovery:
    """Controlled mock tests for user-initiated discovery, cancel, dedupe, and cleanup."""

    def test_discovery_requires_explicit_user_initiation_and_valid_timeout(
        self, tmp_path: Path
    ) -> None:
        sys_bt, sys_rk, dbus = _setup_sysfs(tmp_path)
        backend = LinuxBluetoothBackend(
            sys_bluetooth_dir=sys_bt,
            sys_rfkill_dir=sys_rk,
            dbus_sockets=(dbus,),
            which_fn=lambda _c: "/usr/bin/bluetoothctl",
            command_runner=lambda _a, _t, _c: CommandResult(0, SHOW_ON, ""),
            rfcomm_check=lambda: True,
        )
        engine = BluetoothEngine(platform_info=_linux_platform(), backend=backend)
        try:
            with pytest.raises(BluetoothConsentRequiredError):
                engine.discover_devices(user_initiated=False, timeout_seconds=5.0)
            for bad_timeout in (-3, 0, 1.0, 31.0, True, False, float("nan"), float("inf")):
                with pytest.raises(BluetoothDiscoveryError):
                    validate_discovery_timeout(bad_timeout)  # type: ignore[arg-type]
        finally:
            engine.shutdown()

    def test_discovery_deduplicates_devices_preserves_unnamed_and_runs_scan_off(
        self, tmp_path: Path
    ) -> None:
        sys_bt, sys_rk, dbus = _setup_sysfs(tmp_path)
        commands_seen: list[list[str]] = []

        scan_stdout = (
            "[NEW] Device 11:22:33:44:55:66 Pixel Headset\n"
            "[CHG] Device 11:22:33:44:55:66 RSSI: -58\n"
            "[CHG] Device 11:22:33:44:55:66 RSSI: -51\n"
            "[NEW] Device 77:88:99:AA:BB:CC 77-88-99-AA-BB-CC\n"
            "[CHG] Device 77:88:99:AA:BB:CC Paired: yes\n"
        )
        devices_stdout = (
            "Device 11:22:33:44:55:66 Pixel Headset\n"
            "Device 77:88:99:AA:BB:CC 77-88-99-AA-BB-CC\n"
        )

        def runner(argv, _timeout, _cancel):
            cmd = list(argv)
            commands_seen.append(cmd)
            if "show" in cmd:
                return CommandResult(0, SHOW_ON, "")
            if cmd[-2:] == ["scan", "on"]:
                return CommandResult(0, scan_stdout, "")
            if cmd[-2:] == ["scan", "off"]:
                return CommandResult(0, "Discovery stopped", "")
            if cmd[-1] == "devices":
                return CommandResult(0, devices_stdout, "")
            return CommandResult(0, "", "")

        backend = LinuxBluetoothBackend(
            sys_bluetooth_dir=sys_bt,
            sys_rfkill_dir=sys_rk,
            dbus_sockets=(dbus,),
            which_fn=lambda _c: "/usr/bin/bluetoothctl",
            command_runner=runner,
            rfcomm_check=lambda: True,
        )
        engine = BluetoothEngine(platform_info=_linux_platform(), backend=backend)
        try:
            snap = engine.discover_devices(user_initiated=True, timeout_seconds=5.0)
            assert snap.state is DiscoveryState.COMPLETED
            assert snap.device_count == 2
            assert snap.timeout_seconds == 5.0
            assert snap.started_at is not None and snap.ended_at is not None

            named = engine.get_discovered_device("11:22:33:44:55:66")
            unnamed = engine.get_discovered_device("77:88:99:aa:bb:cc")
            assert named is not None
            assert named.name == "Pixel Headset"
            assert named.rssi == -51  # Updated from duplicate advertisement

            # Unnamed device keeps name=None — never fabricated!
            assert unnamed is not None
            assert unnamed.name is None
            assert unnamed.paired is True

            # Cleanup always invoked `scan off` so no background scan persists.
            assert any(cmd[-2:] == ["scan", "off"] for cmd in commands_seen)
        finally:
            engine.shutdown()

    def test_discovery_empty_results_never_fabricates_devices(
        self, tmp_path: Path
    ) -> None:
        sys_bt, sys_rk, dbus = _setup_sysfs(tmp_path)

        def runner(argv, _t, _c):
            if "show" in argv:
                return CommandResult(0, SHOW_ON, "")
            return CommandResult(0, "", "")

        backend = LinuxBluetoothBackend(
            sys_bluetooth_dir=sys_bt,
            sys_rfkill_dir=sys_rk,
            dbus_sockets=(dbus,),
            which_fn=lambda _c: "/usr/bin/bluetoothctl",
            command_runner=runner,
        )
        engine = BluetoothEngine(platform_info=_linux_platform(), backend=backend)
        try:
            snap = engine.discover_devices(user_initiated=True, timeout_seconds=3.0)
            assert snap.state is DiscoveryState.COMPLETED
            assert snap.device_count == 0
            assert snap.devices == ()
        finally:
            engine.shutdown()

    def test_discovery_cancellation_and_concurrent_scan_guard(
        self, tmp_path: Path
    ) -> None:
        sys_bt, sys_rk, dbus = _setup_sysfs(tmp_path)
        scan_entered = threading.Event()
        scan_off_called = threading.Event()

        def runner(argv, _timeout, cancel_event):
            cmd = list(argv)
            if "show" in cmd:
                return CommandResult(0, SHOW_ON, "")
            if cmd[-2:] == ["scan", "on"]:
                scan_entered.set()
                assert cancel_event is not None
                cancel_event.wait(timeout=5.0)
                return CommandResult(-1, "", "", cancelled=True)
            if cmd[-2:] == ["scan", "off"]:
                scan_off_called.set()
                return CommandResult(0, "", "")
            return CommandResult(0, "", "")

        backend = LinuxBluetoothBackend(
            sys_bluetooth_dir=sys_bt,
            sys_rfkill_dir=sys_rk,
            dbus_sockets=(dbus,),
            which_fn=lambda _c: "/usr/bin/bluetoothctl",
            command_runner=runner,
        )
        engine = BluetoothEngine(platform_info=_linux_platform(), backend=backend)
        try:
            started = engine.start_discovery(user_initiated=True, timeout_seconds=12.0)
            assert started.state is DiscoveryState.SCANNING
            assert scan_entered.wait(timeout=2.0)

            # Reject overlapping scan while active.
            with pytest.raises(BluetoothDiscoveryError):
                engine.start_discovery(user_initiated=True, timeout_seconds=4.0)

            cancelled = engine.cancel_discovery(wait_timeout=3.0)
            assert cancelled.state is DiscoveryState.CANCELLED
            assert cancelled.devices == ()
            assert engine.is_scanning is False
            assert scan_off_called.is_set()
        finally:
            engine.shutdown()

    def test_discovery_timeout_permission_denied_and_disabled_adapter(
        self, tmp_path: Path
    ) -> None:
        sys_bt, sys_rk, dbus = _setup_sysfs(tmp_path)
        scan_timeouts: list[str] = []

        def timeout_runner(argv, _timeout, _cancel):
            cmd = list(argv)
            if "show" in cmd:
                return CommandResult(0, SHOW_ON, "")
            if cmd[-2:] == ["scan", "on"]:
                idx = cmd.index("--timeout")
                scan_timeouts.append(cmd[idx + 1])
                return CommandResult(0, "", "", timed_out=True)
            return CommandResult(0, "", "")

        bt_timeout = LinuxBluetoothBackend(
            sys_bluetooth_dir=sys_bt,
            sys_rfkill_dir=sys_rk,
            dbus_sockets=(dbus,),
            which_fn=lambda _c: "/usr/bin/bluetoothctl",
            command_runner=timeout_runner,
        )
        engine = BluetoothEngine(platform_info=_linux_platform(), backend=bt_timeout)
        try:
            snap = engine.discover_devices(user_initiated=True, timeout_seconds=4.0)
            assert snap.state is DiscoveryState.COMPLETED
            assert snap.timeout_seconds == 4.0
            assert snap.devices == ()
            assert scan_timeouts == ["4"]
        finally:
            engine.shutdown()

        # Permission denied mid-discovery
        from poptrap.engine.bluetooth import BluetoothDisabledError, BluetoothPermissionError

        bt_perm = LinuxBluetoothBackend(
            sys_bluetooth_dir=sys_bt,
            sys_rfkill_dir=sys_rk,
            dbus_sockets=(dbus,),
            which_fn=lambda _c: "/usr/bin/bluetoothctl",
            command_runner=lambda argv, _t, _c: (
                CommandResult(0, SHOW_ON, "")
                if "show" in argv
                else CommandResult(1, "", "org.bluez.Error.NotAuthorized: Permission denied")
            ),
        )
        engine_perm = BluetoothEngine(platform_info=_linux_platform(), backend=bt_perm)
        try:
            with pytest.raises(BluetoothPermissionError):
                engine_perm.discover_devices(user_initiated=True, timeout_seconds=3.0)
            assert engine_perm.discovery_status().state is DiscoveryState.FAILED
        finally:
            engine_perm.shutdown()

        # Disabled adapter rejected before starting discovery
        show_off = "Controller AA:BB:CC:00:11:22\n\tPowered: no\n"
        bt_off = LinuxBluetoothBackend(
            sys_bluetooth_dir=sys_bt,
            sys_rfkill_dir=sys_rk,
            dbus_sockets=(dbus,),
            which_fn=lambda _c: "/usr/bin/bluetoothctl",
            command_runner=lambda _a, _t, _c: CommandResult(0, show_off, ""),
        )
        engine_off = BluetoothEngine(platform_info=_linux_platform(), backend=bt_off)
        try:
            with pytest.raises(BluetoothDisabledError):
                engine_off.discover_devices(user_initiated=True, timeout_seconds=3.0)
        finally:
            engine_off.shutdown()

    def test_adapter_disconnection_mid_discovery_reports_error_and_cleans_up(
        self, tmp_path: Path
    ) -> None:
        sys_bt, sys_rk, dbus = _setup_sysfs(tmp_path)
        scan_off_count = 0

        def runner(argv, _t, _c):
            nonlocal scan_off_count
            cmd = list(argv)
            if "show" in cmd:
                return CommandResult(0, SHOW_ON, "")
            if cmd[-2:] == ["scan", "on"]:
                return CommandResult(1, "No default controller available\n", "")
            if cmd[-2:] == ["scan", "off"]:
                scan_off_count += 1
                return CommandResult(0, "", "")
            return CommandResult(0, "", "")

        backend = LinuxBluetoothBackend(
            sys_bluetooth_dir=sys_bt,
            sys_rfkill_dir=sys_rk,
            dbus_sockets=(dbus,),
            which_fn=lambda _c: "/usr/bin/bluetoothctl",
            command_runner=runner,
        )
        engine = BluetoothEngine(platform_info=_linux_platform(), backend=backend)
        try:
            with pytest.raises(BluetoothAdapterDisconnectedError):
                engine.discover_devices(user_initiated=True, timeout_seconds=4.0)
            status = engine.discovery_status()
            assert status.state is DiscoveryState.FAILED
            assert status.devices == ()
            assert scan_off_count == 1
        finally:
            engine.shutdown()

    def test_run_safe_command_enforces_timeout_and_cancellation_without_leaks(self) -> None:
        timed_out = run_safe_command(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            timeout=0.5,
        )
        assert timed_out.timed_out is True

        cancel = threading.Event()
        timer = threading.Timer(0.15, cancel.set)
        timer.start()
        try:
            cancelled = run_safe_command(
                [sys.executable, "-c", "import time; time.sleep(10)"],
                timeout=10.0,
                cancel_event=cancel,
            )
        finally:
            timer.cancel()
        assert cancelled.cancelled is True


class TestPairingAndConnectionWorkflows:
    """Tests for explicit pairing, rejection, cancel, connection drop, and disconnect."""

    @pytest.mark.parametrize(
        "bad_address",
        [
            "",
            "--timeout=99",
            "AA:BB:CC:DD:EE:GG",
            "AA:BB:CC:DD:EE",
            "AA:BB:CC:DD:EE:FF; rm -rf /",
            "AA:BB:CC:DD:EE:FF\nscan on",
        ],
    )
    def test_malformed_or_injected_device_identifiers_rejected(
        self, bad_address: str
    ) -> None:
        with pytest.raises(BluetoothDeviceNotFoundError):
            normalize_device_address(bad_address)

    def test_pairing_accepted_rejected_cancelled_and_consent_enforced(
        self, tmp_path: Path
    ) -> None:
        sys_bt, sys_rk, dbus = _setup_sysfs(tmp_path)
        mode = {"result": "accept"}
        cancel_cmd_called = {"flag": False}

        def runner(argv, _t, cancel_event):
            cmd = list(argv)
            if "show" in cmd:
                return CommandResult(0, SHOW_ON, "")
            if "cancel-pairing" in cmd:
                cancel_cmd_called["flag"] = True
                return CommandResult(0, "", "")
            if "pair" in cmd:
                if mode["result"] == "accept":
                    return CommandResult(0, "Attempting to pair\nPairing successful\n", "")
                if mode["result"] == "reject":
                    return CommandResult(
                        1, "Failed to pair: org.bluez.Error.AuthenticationRejected\n", ""
                    )
                if mode["result"] == "cancel":
                    assert cancel_event is not None
                    cancel_event.set()
                    return CommandResult(-1, "", "", cancelled=True)
            return CommandResult(0, "", "")

        backend = LinuxBluetoothBackend(
            sys_bluetooth_dir=sys_bt,
            sys_rfkill_dir=sys_rk,
            dbus_sockets=(dbus,),
            which_fn=lambda _c: "/usr/bin/bluetoothctl",
            command_runner=runner,
        )
        engine = BluetoothEngine(platform_info=_linux_platform(), backend=backend)
        try:
            # Silent pairing is forbidden.
            with pytest.raises(BluetoothConsentRequiredError):
                engine.pair_device("11:22:33:44:55:66", user_confirmed=False)

            # Staging shows purpose without pairing.
            staged = engine.prepare_pairing("11:22:33:44:55:66")
            assert staged.state is PairingState.AWAITING_CONFIRMATION
            assert staged.user_confirmed is False
            assert "consent" in staged.purpose.lower()

            # Accepted
            paired = engine.pair_device("11:22:33:44:55:66", user_confirmed=True)
            assert paired.state is PairingState.PAIRED
            assert paired.used_os_dialog is True

            # Rejected
            mode["result"] = "reject"
            with pytest.raises(BluetoothPairingRejectedError):
                engine.pair_device("11:22:33:44:55:66", user_confirmed=True)
            assert engine.pairing_status().state is PairingState.REJECTED

            # Cancelled
            mode["result"] = "cancel"
            with pytest.raises(BluetoothPairingCancelledError):
                engine.pair_device("11:22:33:44:55:66", user_confirmed=True)
            assert engine.pairing_status().state is PairingState.CANCELLED
            assert cancel_cmd_called["flag"] is True
        finally:
            engine.shutdown()

    def test_unsupported_platform_rejects_pairing_and_connection(self) -> None:
        unknown = PlatformInfo(
            os_type=OperatingSystem.UNKNOWN,
            system_name="UnknownOS",
            release="1.0",
            version="1.0",
            architecture="x86_64",
            python_version="3.11.2",
            python_version_tuple=(3, 11, 2),
            is_termux=False,
            is_wsl=False,
        )
        engine = BluetoothEngine(
            platform_info=unknown, backend=UnsupportedPlatformBackend()
        )
        try:
            with pytest.raises(BluetoothUnsupportedError):
                engine.pair_device("11:22:33:44:55:66", user_confirmed=True)
            assert engine.pairing_status().state is PairingState.UNSUPPORTED
            with pytest.raises(BluetoothUnsupportedError):
                engine.connect_device("11:22:33:44:55:66", user_confirmed=True)
            assert engine.connection_status().confirmed is False
        finally:
            engine.shutdown()

    def test_connection_exchange_unexpected_loss_disconnect_and_fd_cleanup(
        self, tmp_path: Path
    ) -> None:
        sys_bt, sys_rk, dbus = _setup_sysfs(tmp_path)
        client_a, peer_a = socket.socketpair()
        client_b, peer_b = socket.socketpair()
        sockets = iter([client_a, client_b])
        os_disconnected: list[str] = []

        def runner(argv, _t, _c):
            if "disconnect" in argv:
                os_disconnected.append(argv[-1])
            return CommandResult(0, SHOW_ON, "")

        backend = LinuxBluetoothBackend(
            sys_bluetooth_dir=sys_bt,
            sys_rfkill_dir=sys_rk,
            dbus_sockets=(dbus,),
            which_fn=lambda _c: "/usr/bin/bluetoothctl",
            command_runner=runner,
            rfcomm_check=lambda: True,
            transport_factory=lambda: _SocketPairRfcommTransport(next(sockets)),
        )
        engine = BluetoothEngine(platform_info=_linux_platform(), backend=backend)

        try:
            # Cannot connect without explicit consent.
            with pytest.raises(BluetoothConsentRequiredError):
                engine.connect_device("11:22:33:44:55:66", user_confirmed=False)

            conn = engine.connect_device("11:22:33:44:55:66", user_confirmed=True)
            assert conn.state is ConnectionState.CONNECTED
            assert conn.confirmed is True
            assert conn.transport is TransportKind.RFCOMM

            # Peer listener validates incoming PTBT1 frame and returns signed-ID ACK.
            received_payloads: list[bytes] = []

            def _peer_responder() -> None:
                body = recv_transport_frame(peer_a, max_body=MAX_INVITATION_BYTES)
                received_payloads.append(body)
                import json as _json

                inv_id = _json.loads(body.decode("utf-8"))["invitation_id"]
                peer_a.sendall(encode_ack_frame(inv_id, status="received"))

            responder = threading.Thread(target=_peer_responder, daemon=True)
            responder.start()

            sent_record = engine.send_invitation(
                "valid_local_session_01",
                user_confirmed=True,
                target_address="11:22:33:44:55:66",
                ttl_seconds=60,
            )
            responder.join(timeout=2.0)

            assert sent_record.delivery_confirmed is True
            assert len(received_payloads) == 1
            live = engine.connection_status()
            assert live.invitations_sent == 1
            assert live.bytes_sent > len(FRAME_MAGIC)
            assert live.bytes_received > len(FRAME_MAGIC)

            # Simulate unexpected remote connection drop -> LOST & socket closed.
            peer_a.close()
            time.sleep(0.02)
            dropped = engine.connection_status()
            assert dropped.state is ConnectionState.LOST
            assert dropped.confirmed is False
            assert client_a.fileno() == -1

            # Reconnect explicitly, then verify explicit disconnect cleans up completely.
            reconn = engine.connect_device("11:22:33:44:55:66", user_confirmed=True)
            assert reconn.state is ConnectionState.CONNECTED
            assert reconn.confirmed is True

            disc = engine.disconnect()
            assert disc.state is ConnectionState.DISCONNECTED
            assert disc.confirmed is False
            assert client_b.fileno() == -1
            assert "11:22:33:44:55:66" in os_disconnected
        finally:
            peer_a.close()
            peer_b.close()
            client_a.close()
            client_b.close()
            engine.shutdown()

    def test_unacknowledged_or_rejected_transfer_never_claims_delivery(
        self, tmp_path: Path
    ) -> None:
        sys_bt, sys_rk, dbus = _setup_sysfs(tmp_path)
        client_s, peer_s = socket.socketpair()
        backend = LinuxBluetoothBackend(
            sys_bluetooth_dir=sys_bt,
            sys_rfkill_dir=sys_rk,
            dbus_sockets=(dbus,),
            which_fn=lambda _c: "/usr/bin/bluetoothctl",
            command_runner=lambda _a, _t, _c: CommandResult(0, SHOW_ON, ""),
            rfcomm_check=lambda: True,
            transport_factory=lambda: _SocketPairRfcommTransport(client_s),
        )
        engine = BluetoothEngine(platform_info=_linux_platform(), backend=backend)

        def _rejecting_peer() -> None:
            body = recv_transport_frame(peer_s, max_body=MAX_INVITATION_BYTES)
            import json as _json

            inv_id = _json.loads(body.decode("utf-8"))["invitation_id"]
            peer_s.sendall(encode_ack_frame(inv_id, status="rejected"))

        t = threading.Thread(target=_rejecting_peer, daemon=True)
        t.start()
        try:
            with pytest.raises((BluetoothTransportError, BluetoothConnectionError)):
                engine.send_invitation(
                    "valid_local_session_02",
                    user_confirmed=True,
                    target_address="11:22:33:44:55:66",
                )
            t.join(timeout=2.0)
            # Failed invitation is automatically cancelled — zero falsely SENT records.
            assert engine.list_invitations(active_only=True) == ()
        finally:
            peer_s.close()
            client_s.close()
            engine.shutdown()
