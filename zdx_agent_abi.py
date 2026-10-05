"""Versioned Pyxel-native agent ABI over named spatial PNG regions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping

from zdx_spatial_frame import SpatialLayout


AGENT_ABI_VERSION = 1

ROLE_PERSISTENT_MEMORY = "persistent_memory"
ROLE_WORKING_MEMORY = "working_memory"
ROLE_MAILBOX_IN = "mailbox_in"
ROLE_MAILBOX_OUT = "mailbox_out"
ROLE_CAPABILITIES = "capabilities"
ROLE_PROVENANCE = "provenance"

AGENT_REGION_ROLES = frozenset({
    ROLE_PERSISTENT_MEMORY,
    ROLE_WORKING_MEMORY,
    ROLE_MAILBOX_IN,
    ROLE_MAILBOX_OUT,
    ROLE_CAPABILITIES,
    ROLE_PROVENANCE,
})

_MIN_ROLE_CAPACITY = {
    # Default mailbox: 36-byte header + one 256-byte slot.
    ROLE_MAILBOX_IN: 292,
    ROLE_MAILBOX_OUT: 292,
    # Capability header plus at least a small grant payload.
    ROLE_CAPABILITIES: 64,
    # Default journal: 68-byte header + one 384-byte slot.
    ROLE_PROVENANCE: 452,
}


@dataclass(frozen=True)
class AgentRegionBinding:
    role: str
    region: str

    def __post_init__(self) -> None:
        if self.role not in AGENT_REGION_ROLES:
            raise ValueError(f"unsupported agent ABI role: {self.role!r}")
        if not isinstance(self.region, str) or not self.region:
            raise ValueError("agent ABI region name must be a non-empty string")


@dataclass(frozen=True)
class SpatialAgentABI:
    """Binds semantic agent roles to non-executable SpatialLayout regions."""

    version: int
    layout_sha256: str
    bindings: tuple[AgentRegionBinding, ...]

    @staticmethod
    def layout_digest(layout: SpatialLayout) -> str:
        payload = json.dumps(
            layout.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(b"zdx-agent-layout-v1\x00" + payload).hexdigest()

    @classmethod
    def create(
        cls,
        layout: SpatialLayout,
        bindings: Mapping[str, str],
        *,
        version: int = AGENT_ABI_VERSION,
    ) -> "SpatialAgentABI":
        if isinstance(version, bool) or not isinstance(version, int):
            raise ValueError("agent ABI version must be an integer")
        items = tuple(
            AgentRegionBinding(role=str(role), region=str(region))
            for role, region in sorted(bindings.items())
        )
        abi = cls(
            version=version,
            layout_sha256=cls.layout_digest(layout),
            bindings=items,
        )
        abi.validate(layout)
        return abi

    @classmethod
    def infer(
        cls,
        layout: SpatialLayout,
        *,
        persistent_region: str | None = None,
    ) -> "SpatialAgentABI":
        available = {region.name for region in layout.regions}
        bindings: dict[str, str] = {}
        if persistent_region:
            bindings[ROLE_PERSISTENT_MEMORY] = persistent_region
        conventions = {
            ROLE_WORKING_MEMORY: ("working_memory", "working"),
            ROLE_MAILBOX_IN: ("mailbox_in", "inbox"),
            ROLE_MAILBOX_OUT: ("mailbox_out", "outbox"),
            ROLE_CAPABILITIES: ("capabilities",),
            ROLE_PROVENANCE: ("provenance", "audit"),
        }
        for role, candidates in conventions.items():
            for candidate in candidates:
                if candidate in available and candidate not in bindings.values():
                    bindings[role] = candidate
                    break
        return cls.create(layout, bindings)

    @classmethod
    def from_dict(cls, payload: Mapping, layout: SpatialLayout) -> "SpatialAgentABI":
        if not isinstance(payload, Mapping):
            raise TypeError("agent ABI metadata must be a mapping")
        allowed = {"version", "layout_sha256", "bindings"}
        unknown = set(payload) - allowed
        if unknown:
            raise ValueError(f"unknown agent ABI fields: {sorted(unknown)}")
        version = payload.get("version")
        if isinstance(version, bool) or not isinstance(version, int):
            raise ValueError("agent ABI version must be an integer")
        raw_bindings = payload.get("bindings")
        if not isinstance(raw_bindings, Mapping):
            raise ValueError("agent ABI bindings must be an object")
        abi = cls(
            version=version,
            layout_sha256=str(payload.get("layout_sha256", "")),
            bindings=tuple(
                AgentRegionBinding(role=str(role), region=str(region))
                for role, region in sorted(raw_bindings.items())
            ),
        )
        abi.validate(layout)
        return abi

    def validate(self, layout: SpatialLayout) -> "SpatialAgentABI":
        if self.version != AGENT_ABI_VERSION:
            raise ValueError(
                f"unsupported agent ABI version {self.version}; expected {AGENT_ABI_VERSION}"
            )
        expected = self.layout_digest(layout)
        if self.layout_sha256 != expected:
            raise ValueError("agent ABI layout hash does not match SpatialLayout")
        roles = [item.role for item in self.bindings]
        regions = [item.region for item in self.bindings]
        if len(set(roles)) != len(roles):
            raise ValueError("agent ABI contains duplicate semantic roles")
        if len(set(regions)) != len(regions):
            raise ValueError("agent ABI cannot bind multiple roles to one region")
        for item in self.bindings:
            region = layout.region(item.region)
            if region.y < layout.execution_rows:
                raise ValueError(f"agent ABI role {item.role!r} overlaps execution plane")
            minimum = _MIN_ROLE_CAPACITY.get(item.role, 1)
            if region.capacity_bytes < minimum:
                raise ValueError(
                    f"agent ABI role {item.role!r} requires at least {minimum} bytes"
                )
        return self

    def region_for(self, role: str) -> str | None:
        for item in self.bindings:
            if item.role == role:
                return item.region
        return None

    def require_region(self, role: str) -> str:
        region = self.region_for(role)
        if region is None:
            raise KeyError(f"agent ABI does not bind role {role!r}")
        return region

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "layout_sha256": self.layout_sha256,
            "bindings": {item.role: item.region for item in self.bindings},
        }
