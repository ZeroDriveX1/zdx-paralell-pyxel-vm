"""Open-Pyxel node daemon foundation."""

import time


class NodeDaemon:
    def __init__(self, node):
        self.node = node
        self.running = False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def run_once(self):
        if not self.running:
            return
        # Future: peer sync, workload execution, proof submission.

    def run_forever(self):
        self.start()
        while self.running:
            self.run_once()
            time.sleep(1)
