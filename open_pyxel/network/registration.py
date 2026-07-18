"""Account and device registration flow for Open-Pyxel nodes."""

from dataclasses import dataclass


@dataclass
class DeviceRegistrationRequest:
    user_id: str
    node_id: str
    public_key: str
    hardware_profile: dict


@dataclass
class DeviceRegistrationResult:
    approved: bool
    node_id: str
    message: str


def register_device(request: DeviceRegistrationRequest) -> DeviceRegistrationResult:
    """Validate a device attachment request.

    Production implementation will add account authentication,
    signature verification, and device approval storage.
    """
    if not request.user_id or not request.node_id:
        return DeviceRegistrationResult(False, request.node_id, "invalid identity")

    return DeviceRegistrationResult(True, request.node_id, "device registered")
