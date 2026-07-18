import time


class NodeHeartbeat:
    def __init__(self, node_id):
        self.node_id = node_id
        self.last_seen = None

    def pulse(self):
        self.last_seen = time.time()
        return {
            "node_id": self.node_id,
            "timestamp": self.last_seen,
            "status": "online",
        }
