"""The FCM push listener revival in ``locations`` -- with every vendored
module stubbed, since the real ones need selenium & co. at import time."""

import importlib
import sys
import types
from unittest.mock import MagicMock

import pytest

_VENDOR_MODULES = [
    "Auth", "Auth.fcm_receiver", "Auth.firebase_messaging",
    "Auth.firebase_messaging.fcmpushclient",
    "NovaApi", "NovaApi.ExecuteAction", "NovaApi.ExecuteAction.LocateTracker",
    "NovaApi.ExecuteAction.LocateTracker.decrypt_locations",
    "NovaApi.ExecuteAction.LocateTracker.location_request",
    "NovaApi.ExecuteAction.PlaySound", "NovaApi.ExecuteAction.PlaySound.sound_request",
    "NovaApi.ListDevices", "NovaApi.ListDevices.nbe_list_devices",
    "NovaApi.nova_request", "NovaApi.scopes", "NovaApi.util",
    "ProtoDecoders", "ProtoDecoders.decoder",
    "KeyBackup", "KeyBackup.cloud_key_decryptor",
    "FMDNCrypto", "FMDNCrypto.foreign_tracker_cryptor",
    "SpotApi", "SpotApi.UploadPrecomputedPublicKeyIds",
    "SpotApi.UploadPrecomputedPublicKeyIds.upload_precomputed_public_key_ids",
]


@pytest.fixture
def locations(monkeypatch):
    for name in _VENDOR_MODULES:
        monkeypatch.setitem(sys.modules, name, MagicMock(name=name))
    run_state = types.SimpleNamespace(STARTED="STARTED", STOPPING="STOPPING", STOPPED="STOPPED")
    sys.modules["Auth.firebase_messaging.fcmpushclient"].FcmPushClientRunState = run_state
    monkeypatch.delitem(sys.modules, "locations", raising=False)
    mod = importlib.import_module("locations")
    yield mod
    sys.modules.pop("locations", None)


def _receiver(listening, run_state):
    r = MagicMock()
    r._listening = listening
    r.pc.run_state = run_state
    r.pc.sequential_error_counters = {"LOGIN": 3}
    return r


def test_dead_listener_is_reset_for_restart(locations):
    r = _receiver(True, "STOPPING")
    old_loop = r._loop
    assert locations._revive_fcm_listener(r) is True
    assert r._listening is False
    assert r.pc.sequential_error_counters == {}
    old_loop.call_soon_threadsafe.assert_called_once_with(old_loop.stop)


def test_healthy_listener_is_left_alone(locations):
    r = _receiver(True, "STARTED")
    assert locations._revive_fcm_listener(r) is False
    assert r._listening is True
    assert r.pc.sequential_error_counters == {"LOGIN": 3}


def test_never_started_listener_is_left_alone(locations):
    r = _receiver(False, "STOPPED")
    assert locations._revive_fcm_listener(r) is False
