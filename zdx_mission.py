"""Scheduled autonomous mission execution through registered inference providers."""

from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from zdx_inference import InferenceProvider, ProviderError
from zdx_metrics import METRICS, MetricsRegistry
from zdx_storage import StateStore

LOGGER = logging.getLogger("zdx.mission")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def default_verifier(response: str, _mission: str) -> tuple[bool, str]:
    if not isinstance(response, str) or not response.strip():
        return False, "response is empty"
    return True, "non-empty response"


@dataclass(frozen=True)
class MissionResult:
    mission_id: str
    mission: str
    prompt: str
    model: str
    response: str
    verification_passed: bool
    verification_reason: str
    reflection_count: int
    latency_seconds: float
    prompt_tokens: int
    completion_tokens: int
    created_at: str


class MissionExecutionError(RuntimeError):
    def __init__(self, mission_id: str, cause: ProviderError):
        super().__init__(f"mission {mission_id} failed: {cause.code}")
        self.mission_id = mission_id
        self.cause = cause


class MissionHistory:
    """Transactional mission audit history, with optional PNG mirroring."""

    def __init__(self, path: str | Path, pixel_memory=None):
        self.store = StateStore(path, "mission-history")
        self.pixel_memory = pixel_memory

    def append(self, record: dict) -> None:
        def add(current):
            history = list(current or [])
            history.append(record)
            return history
        self.store.update(add, default=[])
        if self.pixel_memory is not None:
            self.pixel_memory.remember(f"mission:{record['mission_id']}", record)

    def all(self) -> list[dict]:
        return self.store.load([])

    def relevant_memory(self, limit: int = 5) -> list[dict]:
        history = self.all()
        return history[-max(0, limit):]


class MissionAgent:
    """Agent pipeline; providers can only be reached through this abstraction."""

    def __init__(self, provider: InferenceProvider, history: MissionHistory,
                 *, verifier: Callable[[str, str], tuple[bool, str]] = default_verifier,
                 metrics: MetricsRegistry = METRICS, reflection_enabled: bool | None = None,
                 max_reflection_cycles: int | None = None):
        if not isinstance(provider, InferenceProvider):
            raise TypeError("provider must implement InferenceProvider")
        self.provider = provider
        self.history = history
        self.verifier = verifier
        self.metrics = metrics
        if reflection_enabled is None:
            reflection_enabled = os.environ.get("ZDX_REFLECTION_ENABLED", "false").lower() in {"1", "true", "yes"}
        if max_reflection_cycles is None:
            try:
                max_reflection_cycles = int(os.environ.get("ZDX_REFLECTION_MAX_CYCLES", "2"))
            except ValueError as exc:
                raise ValueError("ZDX_REFLECTION_MAX_CYCLES must be an integer") from exc
        if not 0 <= max_reflection_cycles <= 2:
            raise ValueError("reflection cycles must be between 0 and 2")
        self.reflection_enabled = reflection_enabled
        self.max_reflection_cycles = max_reflection_cycles

    def _prompt(self, mission: str, memory: list[dict]) -> str:
        context = "\n".join(
            f"- {item.get('mission', '')}: {item.get('final_response', '')}"
            for item in memory if item.get("verification_status") is True
        )
        suffix = f"\nRelevant prior verified memory:\n{context}" if context else ""
        return f"Complete this mission safely and directly:\n{mission}{suffix}"

    def execute(self, mission: str) -> MissionResult:
        if not isinstance(mission, str) or not mission.strip():
            raise ValueError("mission must be a non-empty string")
        mission_id = uuid.uuid4().hex
        created_at = _utc_now()
        total_started = time.perf_counter()
        LOGGER.info("mission_received", extra={"mission_id": mission_id})
        memory = self.history.relevant_memory()
        LOGGER.info("memory_loaded", extra={"mission_id": mission_id, "records": len(memory)})
        prompt_started = time.perf_counter()
        prompt = self._prompt(mission, memory)
        self.metrics.observe("prompt_latency", time.perf_counter() - prompt_started)
        LOGGER.info("prompt_built", extra={"mission_id": mission_id})
        reflection_count = 0
        retries = 0
        generation_seconds = 0.0
        response = ""
        verification_passed = False
        verification_reason = "not verified"
        try:
            generation_started = time.perf_counter()
            generated = self.provider.generate(prompt)
            generation_seconds += time.perf_counter() - generation_started
            retries += generated.retries
            response = generated.text
            LOGGER.info("model_response_received", extra={"mission_id": mission_id})
            verification_passed, verification_reason = self.verifier(response, mission)
            LOGGER.info("response_verified", extra={"mission_id": mission_id, "passed": verification_passed})
            while (self.reflection_enabled and not verification_passed and
                   reflection_count < self.max_reflection_cycles):
                reflection_count += 1
                critique_prompt = (
                    f"Critique this response to the mission. Identify concrete defects only.\n"
                    f"Mission: {mission}\nResponse: {response}\nVerifier: {verification_reason}"
                )
                LOGGER.info("critique_started", extra={"mission_id": mission_id, "cycle": reflection_count})
                critique_started = time.perf_counter()
                critique = self.provider.generate(critique_prompt)
                generation_seconds += time.perf_counter() - critique_started
                retries += critique.retries
                revision_prompt = (
                    f"Revise the response using the critique. Return only the revised answer.\n"
                    f"Mission: {mission}\nResponse: {response}\nCritique: {critique.text}"
                )
                LOGGER.info("revision_started", extra={"mission_id": mission_id, "cycle": reflection_count})
                revised_started = time.perf_counter()
                revised = self.provider.generate(revision_prompt)
                generation_seconds += time.perf_counter() - revised_started
                retries += revised.retries
                response = revised.text
                verification_passed, verification_reason = self.verifier(response, mission)
                LOGGER.info("reflection_completed", extra={"mission_id": mission_id, "cycle": reflection_count, "passed": verification_passed})
            total_seconds = time.perf_counter() - total_started
            result = MissionResult(
                mission_id=mission_id, mission=mission, prompt=prompt,
                model=generated.model, response=response,
                verification_passed=verification_passed,
                verification_reason=verification_reason,
                reflection_count=reflection_count,
                latency_seconds=total_seconds,
                prompt_tokens=generated.prompt_tokens or _estimate_tokens(prompt),
                completion_tokens=generated.completion_tokens or _estimate_tokens(response),
                created_at=created_at,
            )
            record = asdict(result)
            record["timestamp"] = created_at
            record["generation_latency_seconds"] = generation_seconds
            record["token_estimates"] = {
                "prompt": result.prompt_tokens, "completion": result.completion_tokens,
            }
            record["verification_status"] = verification_passed
            record["final_response"] = response
            record["retries"] = retries
            self.history.append(record)
            LOGGER.info("mission_persisted", extra={"mission_id": mission_id})
            self.metrics.observe("generation_latency", generation_seconds)
            self.metrics.observe("total_mission_latency", total_seconds)
            self.metrics.observe("mission_duration", total_seconds)
            self.metrics.increment("reflection_count", reflection_count)
            self.metrics.increment("successful_missions" if verification_passed else "failed_missions")
            LOGGER.info("mission_completed", extra={"mission_id": mission_id})
            return result
        except ProviderError as exc:
            total_seconds = time.perf_counter() - total_started
            self.metrics.increment("model_failures")
            self.metrics.increment("failed_missions")
            self.metrics.observe("total_mission_latency", total_seconds)
            failure = {
                "mission_id": mission_id, "mission": mission, "prompt": prompt,
                "model": self.provider.model, "timestamp": created_at,
                "latency_seconds": total_seconds, "token_estimates": {"prompt": _estimate_tokens(prompt), "completion": 0},
                "verification_status": False, "final_response": "",
                "error": exc.as_dict(), "reflection_count": reflection_count,
            }
            self.history.append(failure)
            LOGGER.error("mission_failed", extra={"mission_id": mission_id, "error_code": exc.code})
            raise MissionExecutionError(mission_id, exc) from exc


class MissionExecutor:
    """Public entrypoint enforcing Mission -> Scheduler -> Agent ordering."""

    def __init__(self, scheduler, agent: MissionAgent):
        self.scheduler = scheduler
        self.agent = agent

    def execute(self, mission: str) -> MissionResult:
        return self.scheduler.execute_mission(self.agent, mission)
