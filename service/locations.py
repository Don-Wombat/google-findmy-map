"""
Fetches device lists and locations from the vendored GoogleFindMyTools
library and returns plain, JSON-serializable data instead of printing to
stdout like the upstream CLI does.

Imports below refer to top-level packages (Auth, NovaApi, ProtoDecoders, ...)
that only exist once the vendored repo checkout has been added to sys.path
(see main.py, which does this before importing this module).
"""

import hashlib
import logging
import threading
import time

from Auth.fcm_receiver import FcmReceiver
from Auth.firebase_messaging.fcmpushclient import FcmPushClientRunState
from NovaApi.ExecuteAction.LocateTracker.decrypt_locations import (
    is_mcu_tracker,
    retrieve_identity_key,
)
from NovaApi.ExecuteAction.LocateTracker.location_request import create_location_request
from NovaApi.ExecuteAction.PlaySound.sound_request import create_sound_request
from NovaApi.ListDevices.nbe_list_devices import request_device_list
from NovaApi.nova_request import nova_request
from NovaApi.scopes import NOVA_ACTION_API_SCOPE
from NovaApi.util import generate_random_uuid
from ProtoDecoders import Common_pb2, DeviceUpdate_pb2
from ProtoDecoders.decoder import (
    get_canonic_ids,
    parse_device_list_protobuf,
    parse_device_update_protobuf,
)
from KeyBackup.cloud_key_decryptor import decrypt_aes_gcm
from FMDNCrypto.foreign_tracker_cryptor import decrypt
from SpotApi.UploadPrecomputedPublicKeyIds.upload_precomputed_public_key_ids import (
    refresh_custom_trackers,
)

log = logging.getLogger("findmy-map")

LOCATION_FETCH_TIMEOUT = 25  # seconds to wait for a single device's FCM response
SOUND_REQUEST_GRACE_SECONDS = 2  # hold the FCM registration open long enough to send


def list_devices():
    """Returns [(device_name, canonic_id), ...] for all registered devices."""
    result_hex = request_device_list()
    device_list = parse_device_list_protobuf(result_hex)
    refresh_custom_trackers(device_list)
    return get_canonic_ids(device_list)


def _revive_fcm_listener(receiver) -> bool:
    """Make FcmReceiver start a fresh push listener if the old one died.

    Upstream's FcmPushClient permanently shuts itself down after a few
    sequential connection/login errors (e.g. a short network outage) but
    FcmReceiver keeps `_listening = True`, so it never reconnects: location
    requests still reach Google, the answers are never received, and every
    device reports "no_response" until the process is restarted.
    """
    pc = receiver.pc
    if not receiver._listening:
        return False
    if pc.run_state not in (FcmPushClientRunState.STOPPING, FcmPushClientRunState.STOPPED):
        return False
    log.warning("FCM push listener had shut itself down; starting a new one")
    pc.sequential_error_counters.clear()
    old_loop = receiver._loop
    receiver._listening = False  # next register_for_location_updates() re-registers + restarts
    if old_loop is not None:
        old_loop.call_soon_threadsafe(old_loop.stop)
    return True


def _fetch_device_update(canonic_device_id: str, timeout: float = LOCATION_FETCH_TIMEOUT):
    receiver = FcmReceiver()
    _revive_fcm_listener(receiver)
    request_uuid = generate_random_uuid()
    done = threading.Event()
    holder = {}

    def handle(response_hex):
        device_update = parse_device_update_protobuf(response_hex)
        if device_update.fcmMetadata.requestUuid == request_uuid:
            holder["update"] = device_update
            done.set()

    fcm_token = receiver.register_for_location_updates(handle)
    try:
        hex_payload = create_location_request(canonic_device_id, fcm_token, request_uuid)
        nova_request(NOVA_ACTION_API_SCOPE, hex_payload)
        done.wait(timeout)
    finally:
        # Upstream never removes these callbacks, which leaks one closure per
        # request for the lifetime of the process. We poll repeatedly, so we
        # must clean up or memory/CPU use would grow without bound.
        try:
            receiver.location_update_callbacks.remove(handle)
        except ValueError:
            pass

    return holder.get("update")


def _extract_locations(device_update):
    device_registration = device_update.deviceMetadata.information.deviceRegistration
    identity_key = retrieve_identity_key(device_registration)
    is_mcu = is_mcu_tracker(device_registration)

    info = device_update.deviceMetadata.information.locationInformation.reports.recentLocationAndNetworkLocations
    network_locations = list(info.networkLocations)
    network_times = list(info.networkLocationTimestamps)

    if info.HasField("recentLocation"):
        network_locations.append(info.recentLocation)
        network_times.append(info.recentLocationTimestamp)

    results = []
    for loc, ts in zip(network_locations, network_times):
        if loc.status == Common_pb2.Status.SEMANTIC:
            # "place_name", not "name" -- poll_all_devices() merges this dict
            # straight into a per-device entry that already has a "name" key
            # (the device's own display name); reusing that key here would
            # silently overwrite it with the place name.
            results.append({
                "type": "semantic",
                "place_name": loc.semanticLocation.locationName,
                "time": int(ts.seconds),
            })
            continue

        encrypted_location = loc.geoLocation.encryptedReport.encryptedLocation
        public_key_random = loc.geoLocation.encryptedReport.publicKeyRandom

        if public_key_random == b"":
            identity_key_hash = hashlib.sha256(identity_key).digest()
            decrypted_location = decrypt_aes_gcm(identity_key_hash, encrypted_location)
        else:
            time_offset = 0 if is_mcu else loc.geoLocation.deviceTimeOffset
            decrypted_location = decrypt(identity_key, encrypted_location, public_key_random, time_offset)

        proto_loc = DeviceUpdate_pb2.Location()
        proto_loc.ParseFromString(decrypted_location)

        results.append({
            "type": "geo",
            "latitude": proto_loc.latitude / 1e7,
            "longitude": proto_loc.longitude / 1e7,
            "altitude": proto_loc.altitude,
            "accuracy": loc.geoLocation.accuracy,
            "status": Common_pb2.Status.Name(loc.status),
            "is_own_report": loc.geoLocation.encryptedReport.isOwnReport,
            "time": int(ts.seconds),
        })

    return results


def fetch_device_location(canonic_device_id: str):
    device_update = _fetch_device_update(canonic_device_id)
    if device_update is None:
        return None
    # retrieve_identity_key() calls exit(1) on unrecoverable key-version
    # mismatches (see upstream decrypt_locations.py). That would kill this
    # whole long-running service for a single bad device, so callers must
    # catch SystemExit around this call.
    return _extract_locations(device_update)


def poll_all_devices():
    """Fetches the latest known location for every registered device.

    Returns a list of dicts, one per device, always containing at least
    'name' and 'id'. On success it also contains either a 'geo' fix
    (latitude/longitude/...) or a 'semantic' location ('place_name' only,
    no coordinates -- Google's protocol doesn't carry any for these); on
    failure it contains an 'error' string instead.
    """
    devices = []
    for device_name, canonic_id in list_devices():
        entry = {"name": device_name, "id": canonic_id, "checked_at": int(time.time())}

        try:
            locations = fetch_device_location(canonic_id)
        except SystemExit:
            log.error("Decryption for device %r aborted (see logs above); skipping.", device_name)
            entry["error"] = "decryption_failed"
            devices.append(entry)
            continue
        except Exception:
            log.exception("Failed to fetch location for %r", device_name)
            entry["error"] = "fetch_failed"
            devices.append(entry)
            continue

        if not locations:
            entry["error"] = "no_response"
            devices.append(entry)
            continue

        geo_locations = [loc for loc in locations if loc["type"] == "geo"]
        semantic_locations = [loc for loc in locations if loc["type"] == "semantic"]

        if geo_locations:
            entry.update(max(geo_locations, key=lambda loc: loc["time"]))
        elif semantic_locations:
            entry.update(max(semantic_locations, key=lambda loc: loc["time"]))
        else:
            entry["error"] = "no_location_data"

        devices.append(entry)

    return devices


def _send_sound_request(canonic_device_id: str, should_start: bool) -> bool:
    """Start or stop a device's "find my device" sound.

    Returns whether the request was sent, not whether the device actually
    rang -- Play Sound has no delivery confirmation, unlike a location fetch
    (see _fetch_device_update), so there is nothing to wait on.

    Returns as soon as the push is sent, not after -- callers (the web UI)
    are waiting on this to tell the operator their tap registered, and every
    second added here is a second of that feedback loop. The FCM
    registration still needs to stay open a little longer for the push to
    actually go out, but that grace period and the callback cleanup happen
    in a background thread instead of blocking the response.
    """
    receiver = FcmReceiver()
    _revive_fcm_listener(receiver)

    def handle(_response_hex):
        pass  # no response payload to act on

    def cleanup_after_grace_period():
        time.sleep(SOUND_REQUEST_GRACE_SECONDS)
        # Same leak-avoidance as _fetch_device_update: upstream never
        # removes these callbacks itself.
        try:
            receiver.location_update_callbacks.remove(handle)
        except ValueError:
            pass

    fcm_token = receiver.register_for_location_updates(handle)
    try:
        hex_payload = create_sound_request(should_start, canonic_device_id, fcm_token)
        nova_request(NOVA_ACTION_API_SCOPE, hex_payload)
    except Exception:
        log.exception(
            "Failed to %s the sound on device %r",
            "start" if should_start else "stop", canonic_device_id,
        )
        try:
            receiver.location_update_callbacks.remove(handle)
        except ValueError:
            pass
        return False

    threading.Thread(target=cleanup_after_grace_period, daemon=True).start()
    return True


def start_sound(canonic_device_id: str) -> bool:
    """Make a device play its "find my device" sound."""
    return _send_sound_request(canonic_device_id, should_start=True)


def stop_sound(canonic_device_id: str) -> bool:
    """Stop a device's "find my device" sound started by start_sound()."""
    return _send_sound_request(canonic_device_id, should_start=False)
