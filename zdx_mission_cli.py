"""Environment-configured local mission runner."""
import argparse
import json
import logging
import os
from dataclasses import asdict

from pyxel_registry import PyxelRegistry
from zdx_agent_runtime import ZDXAgentRuntime
from zdx_inference import OllamaProvider
from zdx_metrics import METRICS
from zdx_mission import MissionAgent, MissionHistory
from zdx_scheduler import ZDXScheduler


def main():
    parser = argparse.ArgumentParser(description="Run one scheduled ZeroDriveX Ollama mission")
    parser.add_argument("mission")
    args = parser.parse_args()
    logging.basicConfig(level=os.environ.get("ZDX_LOG_LEVEL", "INFO"))
    provider = OllamaProvider(metrics=METRICS)
    try:
        history = MissionHistory(os.environ.get("ZDX_MISSION_HISTORY", ".zdx/missions.json"))
        registry = PyxelRegistry()
        registry.register("scheduler", ZDXScheduler())
        registry.register("mission_agent", MissionAgent(provider, history))
        result = ZDXAgentRuntime(registry).run_mission(args.mission)
        print(json.dumps(asdict(result), indent=2, sort_keys=True))
    finally:
        provider.close()


if __name__ == "__main__": main()
