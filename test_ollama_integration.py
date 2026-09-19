import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from pyxel_registry import PyxelRegistry
from zdx_agent_runtime import ZDXAgentRuntime
from zdx_inference import (InferenceProvider, InferenceResult, OllamaConfig,
                           OllamaProvider, ProviderError)
from zdx_metrics import METRICS, MetricsRegistry
from zdx_mission import (MissionAgent, MissionExecutionError, MissionExecutor,
                         MissionHistory)
from zdx_scheduler import ZDXScheduler


class StubState:
    def __init__(self):
        self.responses = []
        self.requests = []
        self.lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    state = None

    def log_message(self, *_args):
        pass

    def _send(self, status, payload, content_type="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/tags":
            self._send(200, {"models": [{"name": "qwen2.5:1.5b"}]})
        else:
            self._send(404, {})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        with self.state.lock:
            self.state.requests.append(request)
            response = self.state.responses.pop(0) if self.state.responses else (200, {"response": "ok", "model": request["model"]}, 0)
        status, payload, delay = response
        if delay:
            time.sleep(delay)
        try:
            self._send(status, payload)
        except (BrokenPipeError, ConnectionResetError):
            pass


@pytest.fixture
def ollama_stub():
    state = StubState()
    handler = type("ConfiguredHandler", (Handler,), {"state": state})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_address[1]}"
    yield state, endpoint
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def config(endpoint, **changes):
    values = dict(endpoint=endpoint, model="qwen2.5:1.5b", timeout_seconds=1,
                  max_retries=0, backoff_seconds=0, jitter_seconds=0, pool_size=2,
                  num_predict=64, temperature=0, max_response_bytes=1024 * 1024)
    values.update(changes)
    return OllamaConfig(**values)


def test_environment_defaults_and_overrides():
    defaults = OllamaConfig.from_env({})
    assert defaults.endpoint == "http://localhost:11434"
    assert defaults.model == "qwen2.5:1.5b"
    overridden = OllamaConfig.from_env({"ZDX_OLLAMA_ENDPOINT": "https://example:9999", "ZDX_OLLAMA_MODEL": "custom", "ZDX_OLLAMA_MAX_RETRIES": "4"})
    assert (overridden.endpoint, overridden.model, overridden.max_retries) == ("https://example:9999", "custom", 4)


def test_connectivity_generation_and_pool_reuse(ollama_stub):
    state, endpoint = ollama_stub
    provider = OllamaProvider(config(endpoint))
    assert provider.check_connection()["models"][0]["name"] == "qwen2.5:1.5b"
    state.responses.extend([(200, {"response": "first", "model": "qwen2.5:1.5b", "prompt_eval_count": 4, "eval_count": 2}, 0),
                            (200, {"response": "second", "model": "qwen2.5:1.5b"}, 0)])
    assert provider.generate("mission one").text == "first"
    assert provider.generate("mission two").text == "second"
    assert len(state.requests) == 2
    assert state.requests[0]["options"] == {"num_predict": 64, "temperature": 0}
    assert provider._created == 1
    provider.close()


def test_retry_exponential_backoff_and_metrics(ollama_stub):
    state, endpoint = ollama_stub
    state.responses.extend([(503, {"error": "busy"}, 0), (200, {"response": "recovered"}, 0)])
    delays = []
    metrics = MetricsRegistry()
    provider = OllamaProvider(config(endpoint, max_retries=2, backoff_seconds=.1), sleep=delays.append, metrics=metrics)
    result = provider.generate("retry")
    assert result.text == "recovered" and result.retries == 1
    assert delays == [.1]
    assert metrics.snapshot()["counters"]["model_retries"] == 1


def test_timeout_and_malformed_responses_fail_structurally(ollama_stub):
    state, endpoint = ollama_stub
    state.responses.append((200, {"response": "late"}, .15))
    provider = OllamaProvider(config(endpoint, timeout_seconds=.03))
    with pytest.raises(ProviderError) as timeout:
        provider.generate("slow")
    assert timeout.value.code == "transport_error" and timeout.value.retryable
    state.responses.append((200, b"not-json", 0))
    with pytest.raises(ProviderError) as malformed:
        provider.generate("malformed")
    assert malformed.value.code == "malformed_response"


def test_streaming_ndjson(ollama_stub):
    state, endpoint = ollama_stub
    state.responses.append((200, b'{"response":"a"}\n{"response":"b","done":true}\n', 0))
    provider = OllamaProvider(config(endpoint))
    assert "".join(provider.stream("stream")) == "ab"
    assert state.requests[0]["stream"] is True


class FakeProvider(InferenceProvider):
    def __init__(self, responses=None, error=None):
        self.responses = list(responses or ["answer"])
        self.error = error
        self.calls = []

    @property
    def model(self):
        return "fake:qwen"

    def generate(self, prompt, *, system=None):
        self.calls.append(prompt)
        if self.error:
            raise self.error
        text = self.responses.pop(0)
        return InferenceResult(text, self.model, .001)


class PixelMemorySpy:
    def __init__(self): self.items = {}
    def remember(self, key, value): self.items[key] = value


def test_mission_scheduler_persistence_metrics_and_pixel_memory(tmp_path):
    metrics = MetricsRegistry()
    pixel = PixelMemorySpy()
    history = MissionHistory(tmp_path / "missions.json", pixel)
    provider = FakeProvider(["verified answer"])
    agent = MissionAgent(provider, history, metrics=metrics)
    result = MissionExecutor(ZDXScheduler(), agent).execute("Summarize the system")
    assert result.response == "verified answer" and result.verification_passed
    records = history.all()
    assert records[0]["mission"] == "Summarize the system"
    assert records[0]["model"] == "fake:qwen"
    assert records[0]["verification_status"] is True
    assert "token_estimates" in records[0] and not any("secret" in key for key in records[0])
    assert f"mission:{result.mission_id}" in pixel.items
    snapshot = metrics.snapshot()
    assert snapshot["counters"]["successful_missions"] == 1
    assert snapshot["latencies"]["total_mission_latency"]["count"] == 1
    export = tmp_path / "metrics.json"
    metrics.export_json(export)
    assert json.loads(export.read_text())["counters"]["successful_missions"] == 1
    assert METRICS.snapshot()["latencies"]["mission_scheduler_latency"]["count"] >= 1


def test_memory_is_loaded_into_next_prompt(tmp_path):
    history = MissionHistory(tmp_path / "missions.json")
    provider = FakeProvider(["first answer", "second answer"])
    executor = MissionExecutor(ZDXScheduler(), MissionAgent(provider, history, metrics=MetricsRegistry()))
    executor.execute("first mission")
    executor.execute("second mission")
    assert "first mission: first answer" in provider.calls[1]


def test_reflection_terminates_early_after_success(tmp_path):
    checks = iter([(False, "missing evidence"), (True, "accepted")])
    verifier = lambda _response, _mission: next(checks)
    provider = FakeProvider(["draft", "critique", "revision"])
    agent = MissionAgent(provider, MissionHistory(tmp_path / "missions.json"), verifier=verifier,
                         metrics=MetricsRegistry(), reflection_enabled=True, max_reflection_cycles=2)
    result = MissionExecutor(ZDXScheduler(), agent).execute("mission")
    assert result.reflection_count == 1 and result.response == "revision"
    assert len(provider.calls) == 3


def test_reflection_is_bounded_to_two_cycles(tmp_path):
    provider = FakeProvider(["draft", "critique1", "revision1", "critique2", "revision2"])
    verifier = lambda _response, _mission: (False, "still invalid")
    metrics = MetricsRegistry()
    agent = MissionAgent(provider, MissionHistory(tmp_path / "missions.json"), verifier=verifier,
                         metrics=metrics, reflection_enabled=True, max_reflection_cycles=2)
    result = MissionExecutor(ZDXScheduler(), agent).execute("mission")
    assert result.reflection_count == 2 and len(provider.calls) == 5
    assert metrics.snapshot()["counters"]["failed_missions"] == 1


def test_provider_failure_persists_safely_and_scheduler_remains_usable(tmp_path):
    error = ProviderError("transport_error", "offline", retryable=True, attempts=3)
    history = MissionHistory(tmp_path / "missions.json")
    scheduler = ZDXScheduler()
    executor = MissionExecutor(scheduler, MissionAgent(FakeProvider(error=error), history, metrics=MetricsRegistry()))
    with pytest.raises(MissionExecutionError):
        executor.execute("mission")
    assert history.all()[0]["error"]["code"] == "transport_error"
    healthy = MissionExecutor(scheduler, MissionAgent(FakeProvider(["ok"]), history, metrics=MetricsRegistry()))
    assert healthy.execute("recovery").response == "ok"


def test_existing_agent_runtime_registry_mission_path(tmp_path):
    registry = PyxelRegistry()
    registry.register("scheduler", ZDXScheduler())
    registry.register("mission_agent", MissionAgent(
        FakeProvider(["registry result"]), MissionHistory(tmp_path / "missions.json"),
        metrics=MetricsRegistry(),
    ))
    result = ZDXAgentRuntime(registry).run_mission("registry mission")
    assert result.response == "registry result"


def test_remote_plaintext_and_endpoint_credentials_are_rejected():
    with pytest.raises(ValueError, match="loopback"):
        OllamaConfig.from_env({"ZDX_OLLAMA_ENDPOINT": "http://example.com:11434"})
    with pytest.raises(ValueError, match="credentials"):
        OllamaConfig.from_env({"ZDX_OLLAMA_ENDPOINT": "https://user:secret@example.com"})


def test_response_size_limit_fails_closed(ollama_stub):
    state, endpoint = ollama_stub
    state.responses.append((200, {"response": "x" * 2048}, 0))
    provider = OllamaProvider(config(endpoint, max_response_bytes=1024))
    with pytest.raises(ProviderError) as failure:
        provider.generate("bounded")
    assert failure.value.code == "response_too_large"
