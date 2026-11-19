"""Bounded local file handling and mocked Bluetooth lifecycle regressions."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from poptrap.app import PopTrapApp
from poptrap.core.config import MAX_CONFIG_BYTES, AppConfig, load_config, parse_toml_file
from poptrap.core.exceptions import ConfigFormatError, PresetFormatError, PresetStorageError
from poptrap.core.logging_setup import configure_logging, get_logger, shutdown_logging
from poptrap.engine.bluetooth.connection import ConnectionManager, RfcommSocketTransport, encode_ack_frame
from poptrap.engine.bluetooth.errors import (
    BluetoothConnectionError, BluetoothTransportError, InvitationCancelledError,
    InvitationDuplicateError, InvitationExpiredError, InvitationReplayError, InvitationValidationError,
)
from poptrap.engine.bluetooth.invitations import InvitationManager, compute_invitation_integrity
from poptrap.engine.bluetooth.models import ConnectionState, TransportKind
from poptrap.engine.models import TemplateId
from poptrap.engine.registry import create_default_registry
from poptrap.engine.studio import PresetStore, build_default_studio_config
from poptrap.engine.studio.presets import MAX_PRESET_DIRECTORY_ENTRIES, MAX_PRESET_FILE_BYTES

SID = "local_session_reference_4901"
PEER = "11:22:33:44:55:66"
KEY = b"K" * 32


@pytest.mark.parametrize("state", ["accepted", "cancelled"])
def test_replay_tombstones_survive_history_eviction_until_expiry_and_clock_rollback(state):
    tick = [1000.0]
    invitations = InvitationManager(signing_key=KEY, epoch_clock=lambda: tick[0],
                                    max_tracked=4, max_replay_entries=16)
    for _ in range(16):
        record, wire = invitations.create_invitation(SID, user_confirmed=True, ttl_seconds=60)
        if state == "accepted":
            invitations.accept_invitation(wire, recipient_confirmed=True)
        else:
            invitations.cancel_invitation(record.envelope.invitation_id)
        if _ == 0:
            first = wire
    with pytest.raises(InvitationDuplicateError, match="security window"):
        invitations.create_invitation(SID, user_confirmed=True, ttl_seconds=60)
    invitations.clear()
    expected = InvitationReplayError if state == "accepted" else InvitationCancelledError
    with pytest.raises(expected):
        invitations.register_incoming(first)
    tick[0] += 60
    invitations.expire_due()
    invitations.create_invitation(SID, user_confirmed=True, ttl_seconds=60)
    tick[0] -= 60
    with pytest.raises(InvitationExpiredError):
        invitations.accept_invitation(first, recipient_confirmed=True)


def test_bluetooth_signed_input_must_have_unique_keys_and_bounded_validity():
    clock = lambda: 1000.0
    sender = InvitationManager(signing_key=KEY, epoch_clock=clock)
    receiver = InvitationManager(signing_key=KEY, epoch_clock=clock)
    _, wire = sender.create_invitation(SID, user_confirmed=True, ttl_seconds=60)
    ambiguous = wire[:-1] + b',"expires_at":1060}'
    with pytest.raises(InvitationValidationError, match="unambiguous"):
        receiver.register_incoming(ambiguous)
    packet = json.loads(wire)
    packet["expires_at"] = 1301
    packet["integrity"] = compute_invitation_integrity(
        signing_key=KEY, **{key: value for key, value in packet.items() if key != "integrity"},
    )
    with pytest.raises(InvitationValidationError, match="maximum lifetime"):
        receiver.register_incoming(json.dumps(packet))
    assert receiver.list_invitations() == ()
    assert receiver.register_incoming(wire).envelope.session_ref == SID


class _SocketStub:
    """A deterministic fake transport. No Bluetooth radio or OS device access."""
    def __init__(self, reply: bytes, advance=lambda: None):
        self.reply = bytearray(reply)
        self.closed = False
        self.advance = advance
        self.sent = False

    def settimeout(self, _timeout):
        pass

    def connect(self, _target):
        pass

    def sendall(self, _frame):
        self.sent = True

    def recv(self, _limit):
        self.advance()
        return bytes((self.reply.pop(0),)) if self.reply else b""

    def fileno(self):
        return -1 if self.closed else 24

    def shutdown(self, _how):
        pass

    def close(self):
        self.closed = True


def test_bluetooth_ack_total_deadline_and_socket_cleanup(monkeypatch):
    tick = [0.0]
    monkeypatch.setattr("poptrap.engine.bluetooth.connection.time", SimpleNamespace(monotonic=lambda: tick[0]))

    def progress():
        tick[0] += 0.2

    fake = _SocketStub(encode_ack_frame("ack_test"), advance=progress)
    transport = RfcommSocketTransport(socket_factory=lambda: fake)
    transport.connect(PEER)
    with pytest.raises(BluetoothConnectionError, match="timed out"):
        transport.exchange_invitation("ack_test", b"test", timeout=1.0)
    assert fake.sent and fake.closed and not transport.is_connected()


def test_malformed_peer_ack_closes_transport():
    fake = _SocketStub(encode_ack_frame("different_id"))
    transport = RfcommSocketTransport(socket_factory=lambda: fake)
    transport.connect(PEER)
    with pytest.raises(BluetoothTransportError, match="ID mismatch"):
        transport.exchange_invitation("ack_test", b"test")
    assert fake.closed and not transport.is_connected()


def test_acknowledgement_is_never_requested_from_a_different_confirmed_peer():
    class VerifiedTransport:
        transport_kind = TransportKind.RFCOMM
        open = False

        def is_supported(self):
            return True, "mock"

        def connect(self, _target, **_):
            self.open = True

        def is_connected(self):
            return self.open

        def probe_alive(self):
            return self.open

        def exchange_invitation(self, *_args, **_kwargs):
            pytest.fail("The operation reached an unconfirmed target")

        def close(self):
            self.open = False

    manager = ConnectionManager(transport_factory=VerifiedTransport)
    try:
        manager.connect("22:33:44:55:66:77", user_confirmed=True)
        with pytest.raises(BluetoothConnectionError, match="peer changed"):
            manager.send_invitation_payload("ack_test", b"test", expected_address=PEER)
        assert manager.check_status().state is ConnectionState.CONNECTED
    finally:
        manager.close()


def test_oversized_config_falls_back_and_invalid_local_presets_are_skipped(tmp_path):
    config = tmp_path / "large.toml"
    config.write_bytes(b"#" + b" " * MAX_CONFIG_BYTES)
    with pytest.raises(ConfigFormatError, match="exceeds"):
        parse_toml_file(config)
    loaded = load_config(config)
    assert loaded.used_fallback and not loaded.loaded_user_config
    registry = create_default_registry()
    store = PresetStore(tmp_path / "presets", registry=registry)
    sample = store.save_preset(build_default_studio_config(TemplateId.GLITCH, registry=registry))
    for slug, data in (
        ("duplicate-key", b'{"config": {}, "config": {}}'),
        ("bad-depth", b"[" * 2000 + b"]" * 2000),
        ("too-large", b" " * (MAX_PRESET_FILE_BYTES + 1)),
    ):
        (store.presets_dir / f"{slug}.json").write_bytes(data)
        with pytest.raises(PresetFormatError):
            store.load_preset(slug)
    records, warnings = store.list_presets()
    assert [r.preset_id for r in records] == [sample.preset_id] and len(warnings) == 3


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX special files")
def test_non_regular_local_input_is_never_read(tmp_path):
    fifo = tmp_path / "settings.toml"
    os.mkfifo(fifo)
    with pytest.raises(ConfigFormatError, match="regular file"):
        parse_toml_file(fifo)
    assert not configure_logging(log_path=fifo).file_active


def test_logging_never_records_session_capabilities_or_message_text(tmp_path):
    log = tmp_path / "poptrap.log"
    log.touch(mode=0o644)
    if os.name == "posix":
        log.chmod(0o644)
    assert configure_logging(log_path=log, level="DEBUG").file_active
    app = PopTrapApp(config=AppConfig(), config_path=tmp_path / "config.toml")
    try:
        controller = app.prank_controller
        sample = controller.registry.get(TemplateId.GLITCH).build_default_config().model_copy(
            update={"title": "PRIVATE_LABEL_ON_DISK"},
        )
        session, url = app.launch_prank_session(sample)
        controller.activate_session(session.session_id)
        controller.stop_session(session.session_id, reason="PRIVATE_REASON_ON_DISK")
        assert app.issue_studio_url().split("#ticket=")[1]
    finally:
        app._shutdown_runtime()
        shutdown_logging()
    text = log.read_text(encoding="utf-8")
    assert "Session ended: state=stopped" in text
    for secret in (url, session.session_id, "PRIVATE_LABEL_ON_DISK", "PRIVATE_REASON_ON_DISK"):
        assert secret not in text
    if os.name == "posix":
        assert stat.S_IMODE(log.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="Requires POSIX symlinks")
def test_logging_does_not_open_symlink(tmp_path):
    outside = tmp_path / "keep.txt"
    outside.write_text("unchanged", encoding="utf-8")
    log = tmp_path / "poptrap.log"
    log.symlink_to(outside)
    try:
        assert not configure_logging(log_path=log).file_active
        get_logger().warning("test line")
        assert outside.read_text(encoding="utf-8") == "unchanged"
    finally:
        shutdown_logging()


def test_preset_directory_budget_blocks_library_writes_until_recovered(tmp_path):
    registry = create_default_registry()
    root = tmp_path / "presets"
    store = PresetStore(root, registry=registry)
    config = build_default_studio_config(TemplateId.GLITCH, registry=registry)
    record = store.save_preset(config)
    for i in range(MAX_PRESET_DIRECTORY_ENTRIES):
        (root / f"extra-{i}.txt").touch()
    summaries, warnings = store.list_presets()
    assert not summaries and "too many entries" in warnings[0]
    with pytest.raises(PresetStorageError, match="too many entries"):
        store.save_preset(config, overwrite=True)
    assert store.load_preset(record.preset_id) == record
    (root / "extra-0.txt").unlink()
    assert [item.preset_id for item in store.list_presets()[0]] == [record.preset_id]
