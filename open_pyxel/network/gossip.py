"""Peer gossip propagation foundation for decentralized networking."""

from dataclasses import dataclass


@dataclass(frozen=True)
class GossipMessage:
    message_id: str
    sender_id: str
    payload: dict


class GossipRouter:
    def __init__(self):
        self.seen_messages: set[str] = set()

    def accept(self, message: GossipMessage) -> bool:
        if message.message_id in self.seen_messages:
            return False
        self.seen_messages.add(message.message_id)
        return True
