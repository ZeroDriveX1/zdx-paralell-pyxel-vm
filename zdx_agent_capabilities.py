"""Default-deny capability table and action authorization for Pyxel agents."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import struct
from dataclasses import dataclass
from typing import Callable, Iterable, Mapping

from zdx_agent_abi import ROLE_CAPABILITIES


_CAP_MAGIC = b"ZDXCAP1\x00"
_CAP_VERSION = 1
_CAP_HEADER = struct.Struct(">8sBBHI32s")
_SAFE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_ACTION_DOMAIN = b"zdx-agent-action-v1\x00"
_ALLOWED_DECISIONS = frozenset({"allow", "deny", "approval_required"})


def _u16(value: int) -> bytes:
    if not 0 <= value <= 0xFFFF:
        raise ValueError("capability table field exceeds uint16")
    return struct.pack(">H", value)


@dataclass(frozen=True)
class CapabilityGrant:
    name: str
    actions: tuple[str, ...]
    approval_required: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _SAFE.fullmatch(self.name):
            raise ValueError("capability name is invalid")
        raw_actions = tuple(self.actions)
        if not raw_actions:
            raise ValueError("capability grant requires at least one exact action")
        if any(not isinstance(action, str) or not _SAFE.fullmatch(action) for action in raw_actions):
            raise ValueError("capability action name is invalid")
        normalized = tuple(sorted(set(raw_actions)))
        if normalized != self.actions:
            object.__setattr__(self, "actions", normalized)


@dataclass(frozen=True)
class ActionEnvelope:
    capability: str
    action: str
    arguments_sha256: str
    vm_generation: int
    vm_checkpoint_hash: str
    artifact_sha256: str

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "capability": self.capability,
            "action": self.action,
            "arguments_sha256": self.arguments_sha256,
            "vm_generation": self.vm_generation,
            "vm_checkpoint_hash": self.vm_checkpoint_hash,
            "artifact_sha256": self.artifact_sha256,
        }


@dataclass(frozen=True)
class CapabilityDecision:
    status: str
    reason: str
    action_hash: str
    envelope: ActionEnvelope | None
    decision_artifact_sha256: str | None = None

    @property
    def allowed(self) -> bool:
        return self.status == "allow"


def _encode_grants(grants: Iterable[CapabilityGrant]) -> bytes:
    ordered = sorted(grants, key=lambda item: item.name)
    if len(ordered) > 0xFFFF:
        raise ValueError("too many capability grants")
    out = bytearray(_u16(len(ordered)))
    for grant in ordered:
        name = grant.name.encode("utf-8")
        out += _u16(len(name)) + name
        out.append(1 if grant.approval_required else 0)
        out += _u16(len(grant.actions))
        for action_text in grant.actions:
            action = action_text.encode("utf-8")
            out += _u16(len(action)) + action
    return bytes(out)


def _decode_grants(payload: bytes) -> tuple[CapabilityGrant, ...]:
    def take(offset: int, length: int) -> tuple[bytes, int]:
        end = offset + length
        if length < 0 or end > len(payload):
            raise ValueError("truncated capability table")
        return payload[offset:end], end

    def read_u16(offset: int) -> tuple[int, int]:
        raw, offset = take(offset, 2)
        return struct.unpack(">H", raw)[0], offset

    count, offset = read_u16(0)
    grants = []
    for _ in range(count):
        name_len, offset = read_u16(offset)
        name_raw, offset = take(offset, name_len)
        flag_raw, offset = take(offset, 1)
        if flag_raw[0] not in (0, 1):
            raise ValueError("invalid capability approval flag")
        action_count, offset = read_u16(offset)
        actions = []
        for _ in range(action_count):
            action_len, offset = read_u16(offset)
            action_raw, offset = take(offset, action_len)
            actions.append(action_raw.decode("utf-8"))
        grants.append(CapabilityGrant(
            name=name_raw.decode("utf-8"),
            actions=tuple(actions),
            approval_required=bool(flag_raw[0]),
        ))
    if offset != len(payload):
        raise ValueError("trailing bytes in capability table")
    return tuple(grants)


class AgentCapabilityGateway:
    """Prepare state-bound action decisions without executing the action."""

    def __init__(
        self,
        grants: Iterable[CapabilityGrant] = (),
        *,
        evaluator: Callable[[ActionEnvelope, CapabilityGrant], str | bool] | None = None,
    ):
        items = tuple(grants)
        by_name = {item.name: item for item in items}
        if len(by_name) != len(items):
            raise ValueError("duplicate capability grant")
        self.grants = by_name
        self.evaluator = evaluator

    @property
    def manifest_sha256(self) -> str:
        return hashlib.sha256(_encode_grants(self.grants.values())).hexdigest()

    def install(self, session) -> str | None:
        region = session.abi.region_for(ROLE_CAPABILITIES)
        if region is None:
            return None
        payload = _encode_grants(self.grants.values())
        digest = hashlib.sha256(payload).digest()
        document = _CAP_HEADER.pack(
            _CAP_MAGIC, _CAP_VERSION, 0, 0, len(payload), digest
        ) + payload
        capacity = session.frame.layout.region(region).capacity_bytes
        if len(document) > capacity:
            raise ValueError(
                f"capability table requires {len(document)} bytes; "
                f"region capacity is {capacity}"
            )
        session.frame.write_bytes(b"\x00" * capacity, region=region)
        session.frame.write_bytes(document, region=region)
        session.mark_dirty(ROLE_CAPABILITIES)
        session.record_event(
            "capability.install", self.manifest_sha256.encode("ascii")
        )
        return self.manifest_sha256

    def _read_installed(self, session) -> tuple[CapabilityGrant, ...] | None:
        region = session.abi.region_for(ROLE_CAPABILITIES)
        if region is None:
            return None
        capacity = session.frame.layout.region(region).capacity_bytes
        if capacity < _CAP_HEADER.size:
            raise ValueError("capability ABI region is too small")
        header = session.frame.read_bytes(_CAP_HEADER.size, region=region)
        if header == b"\x00" * _CAP_HEADER.size:
            return ()
        magic, version, flags, reserved, length, digest = _CAP_HEADER.unpack(header)
        if magic != _CAP_MAGIC or version != _CAP_VERSION or flags != 0 or reserved != 0:
            raise ValueError("corrupt or unsupported capability table")
        if length > capacity - _CAP_HEADER.size:
            raise ValueError("capability table length exceeds region")
        payload = session.frame.read_bytes(
            length, region=region, offset=_CAP_HEADER.size
        )
        if not secrets.compare_digest(hashlib.sha256(payload).digest(), digest):
            raise ValueError("capability table digest mismatch")
        return _decode_grants(payload)

    def verify_installed(self, session) -> bool:
        installed = self._read_installed(session)
        if installed is None:
            return True
        return tuple(sorted(installed, key=lambda item: item.name)) == tuple(
            sorted(self.grants.values(), key=lambda item: item.name)
        )

    @staticmethod
    def _arguments_bytes(arguments: Mapping) -> bytes:
        if not isinstance(arguments, Mapping):
            raise TypeError("action arguments must be a mapping")
        try:
            return json.dumps(
                dict(arguments),
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("action arguments must be canonical JSON-compatible values") from exc

    @staticmethod
    def _action_hash(envelope: ActionEnvelope) -> str:
        encoded = json.dumps(
            envelope.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(_ACTION_DOMAIN + encoded).hexdigest()

    def decide(
        self,
        session,
        *,
        capability: str,
        action: str,
        arguments: Mapping,
    ) -> CapabilityDecision:
        if not isinstance(capability, str) or not _SAFE.fullmatch(capability):
            raise ValueError("capability name is invalid")
        if not isinstance(action, str) or not _SAFE.fullmatch(action):
            raise ValueError("action name is invalid")
        args = self._arguments_bytes(arguments)
        grant = self.grants.get(capability)
        if grant is None:
            session.record_event("capability.deny", capability.encode("utf-8"))
            return CapabilityDecision("deny", "capability is not granted", "", None)
        if action not in grant.actions:
            session.record_event(
                "capability.deny", f"{capability}:{action}".encode("utf-8")
            )
            return CapabilityDecision("deny", "action is not granted", "", None)
        try:
            installed_ok = self.verify_installed(session)
        except Exception as exc:
            session.record_event(
                "capability.deny", f"table-error:{type(exc).__name__}".encode("ascii")
            )
            return CapabilityDecision("deny", "capability table verification failed", "", None)
        if not installed_ok:
            session.record_event("capability.deny", b"capability-table-mismatch")
            return CapabilityDecision("deny", "installed capability table does not match gateway", "", None)

        prestate = session.checkpoint(barrier=True)
        artifact_sha = prestate.get("artifact_sha256")
        if not artifact_sha:
            raise RuntimeError("barrier checkpoint did not produce artifact identity")
        envelope = ActionEnvelope(
            capability=capability,
            action=action,
            arguments_sha256=hashlib.sha256(args).hexdigest(),
            vm_generation=int(prestate["generation"]),
            vm_checkpoint_hash=str(prestate["checkpoint_hash"]),
            artifact_sha256=str(artifact_sha),
        )
        action_hash = self._action_hash(envelope)
        status = "approval_required" if grant.approval_required else "allow"
        reason = "static capability grant"

        if self.evaluator is not None:
            try:
                evaluated = self.evaluator(envelope, grant)
            except Exception:
                status = "deny"
                reason = "policy evaluator failed closed"
            else:
                if isinstance(evaluated, bool):
                    status = "allow" if evaluated else "deny"
                elif isinstance(evaluated, str) and evaluated in _ALLOWED_DECISIONS:
                    status = evaluated
                else:
                    status = "deny"
                    reason = "policy evaluator returned invalid decision"

        record = session.record_event(
            f"capability.{status}",
            action_hash.encode("ascii"),
            generation=envelope.vm_generation,
        )
        decision_artifact = artifact_sha
        if record is not None:
            decision_artifact = session.checkpoint(barrier=True).get(
                "artifact_sha256"
            )
        return CapabilityDecision(
            status=status,
            reason=reason,
            action_hash=action_hash,
            envelope=envelope,
            decision_artifact_sha256=decision_artifact,
        )
