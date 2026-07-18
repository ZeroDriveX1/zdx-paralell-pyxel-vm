"""Transport layer foundation for Open-Pyxel peer communication.

The transport layer is intentionally separate from protocol logic so
future implementations can support TCP, QUIC, WebRTC, or other P2P
transports without changing node behavior.
"""

from dataclasses import dataclass


@dataclass
class PeerEndpoint:
    node_id: str
    address: str
    port: int


class Transport:
    def send(self, peer: PeerEndpoint, message: dict) -> bool:
        """Send an authenticated protocol message.

        Network encryption and authentication are added by the final
        production transport implementation.
        """
        return bool(peer.node_id and message)
