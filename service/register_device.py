"""Registers a new generic BLE/FMDN tracker (e.g. this project's own ESP32
firmware, see main_battery_status.c) with Google's Find My Device network.

Reimplements SpotApi.CreateBleDevice.create_ble_device.register_esp32() from
the vendored library instead of calling it directly: that function hardcodes
the device's display name ("GoogleFindMyTools µC") with no parameter to
override it. Everything else here -- key generation, protobuf field values,
the CreateBleDevice API call itself -- matches the vendor function line for
line. If GFM_UPSTREAM_REF is ever bumped, re-diff against
SpotApi/CreateBleDevice/create_ble_device.py for upstream changes.

Imports below refer to top-level packages (FMDNCrypto, KeyBackup,
ProtoDecoders, SpotApi, ...) that only exist once the vendored repo checkout
has been added to sys.path (see main.py, which does this before importing
this module) -- same convention as service/locations.py.
"""

import secrets
import time

from Auth.token_cache import get_cached_value
from FMDNCrypto.key_derivation import FMDNOwnerOperations
from FMDNCrypto.eid_generator import ROTATION_PERIOD, generate_eid
from KeyBackup.cloud_key_decryptor import encrypt_aes_gcm
from ProtoDecoders.DeviceUpdate_pb2 import (
    DeviceComponentInformation,
    PublicKeyIdList,
    RegisterBleDeviceRequest,
    SpotDeviceType,
)
from SpotApi.CreateBleDevice.config import mcu_fast_pair_model_id, max_truncated_eid_seconds_server
from SpotApi.CreateBleDevice.util import flip_bits
from SpotApi.GetEidInfoForE2eeDevices.get_owner_key import get_owner_key
from SpotApi.spot_request import spot_request

DEFAULT_DEVICE_NAME = "GoogleFindMyTools µC"
MAX_NAME_LENGTH = 100


def register_tracker(name: str = "") -> str:
    """Register a new generic BLE tracker; return its advertisement key (the
    EID -- an ephemeral identifier derived from a fresh, per-tracker identity
    key, not the identity key itself -- hex-encoded). The key is shown to
    the operator exactly once -- it must be flashed into the tracker's own
    firmware -- and is never written to secrets.json or logged by this app.

    Requires secrets.json to already have a cached owner_key (i.e. the setup
    wizard or an existing GoogleFindMyTools login has completed at least
    once) -- this app has no browser, so a missing owner_key raises plainly
    here instead of falling through to get_owner_key()'s own interactive-
    login fallback, which would otherwise block on a vendored input() call
    this process (no TTY, no Chromium) can never satisfy.
    """
    if not get_cached_value("owner_key"):
        raise RuntimeError(
            "no cached owner_key in secrets.json -- run the setup wizard "
            "(or an existing GoogleFindMyTools login) at least once first"
        )
    owner_key = get_owner_key()
    display_name = (name or "").strip()[:MAX_NAME_LENGTH] or DEFAULT_DEVICE_NAME

    eik = secrets.token_bytes(32)
    eid = generate_eid(eik, 0)
    pair_date = int(time.time())

    register_request = RegisterBleDeviceRequest()
    register_request.fastPairModelId = mcu_fast_pair_model_id

    # Description
    register_request.description.userDefinedName = display_name
    register_request.description.deviceType = SpotDeviceType.DEVICE_TYPE_BEACON

    # Device Components Information
    component_information = DeviceComponentInformation()
    component_information.imageUrl = "https://docs.espressif.com/projects/esp-idf/en/v4.3/esp32/_images/esp32-DevKitM-1-isometric.png"
    register_request.description.deviceComponentsInformation.append(component_information)

    # Capabilities
    register_request.capabilities.isAdvertising = True
    register_request.capabilities.trackableComponents = 1
    register_request.capabilities.capableComponents = 1

    # E2EE Registration
    register_request.e2eePublicKeyRegistration.rotationExponent = 10
    register_request.e2eePublicKeyRegistration.pairingDate = pair_date

    # Encrypted User Secrets
    # Flip bits so Android devices cannot decrypt the key
    register_request.e2eePublicKeyRegistration.encryptedUserSecrets.encryptedIdentityKey = flip_bits(
        encrypt_aes_gcm(owner_key, eik), True
    )

    # Random keys, not used for ESP
    register_request.e2eePublicKeyRegistration.encryptedUserSecrets.encryptedAccountKey = secrets.token_bytes(44)
    register_request.e2eePublicKeyRegistration.encryptedUserSecrets.encryptedSha256AccountKeyPublicAddress = (
        secrets.token_bytes(60)
    )

    register_request.e2eePublicKeyRegistration.encryptedUserSecrets.ownerKeyVersion = 1
    register_request.e2eePublicKeyRegistration.encryptedUserSecrets.creationDate.seconds = pair_date

    time_counter = pair_date
    truncated_eid = eid[:10]

    # announce advertisements
    for _ in range(int(max_truncated_eid_seconds_server / ROTATION_PERIOD)):
        pub_key_id = PublicKeyIdList.PublicKeyIdInfo()
        pub_key_id.publicKeyId.truncatedEid = truncated_eid
        pub_key_id.timestamp.seconds = time_counter
        register_request.e2eePublicKeyRegistration.publicKeyIdList.publicKeyIdInfo.append(pub_key_id)

        time_counter += ROTATION_PERIOD

    # General
    register_request.manufacturerName = "GoogleFindMyTools"
    register_request.modelName = "µC"

    owner_keys = FMDNOwnerOperations()
    owner_keys.generate_keys(identity_key=eik)

    register_request.ringKey = owner_keys.ringing_key
    register_request.recoveryKey = owner_keys.recovery_key
    register_request.unwantedTrackingKey = owner_keys.tracking_key

    spot_request("CreateBleDevice", register_request.SerializeToString())

    return eid.hex()
