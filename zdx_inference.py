"""Inference-provider abstraction and dependency-free Ollama HTTP client."""

from __future__ import annotations

import abc
import http.client
import ipaddress
import json
import os
import queue
import random
import threading
import time
from dataclasses import dataclass
from typing import Iterator
from urllib.parse import urlsplit


@dataclass(frozen=True)
class InferenceResult:
    text: str
    model: str
    latency_seconds: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    retries: int = 0


class ProviderError(RuntimeError):
    """Structured, safe-to-log provider failure."""

    def __init__(self, code: str, message: str, *, retryable: bool = False,
                 attempts: int = 1, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.attempts = attempts
        self.status = status

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "message": str(self),
            "retryable": self.retryable,
            "attempts": self.attempts,
            "status": self.status,
        }


class InferenceProvider(abc.ABC):
    @property
    @abc.abstractmethod
    def model(self) -> str: ...

    @abc.abstractmethod
    def generate(self, prompt: str, *, system: str | None = None) -> InferenceResult: ...

    def stream(self, prompt: str, *, system: str | None = None) -> Iterator[str]:
        yield self.generate(prompt, system=system).text

    def close(self) -> None:
        return None


@dataclass(frozen=True)
class OllamaConfig:
    endpoint: str = "http://localhost:11434"
    model: str = "qwen2.5:1.5b"
    timeout_seconds: float = 30.0
    max_retries: int = 2
    backoff_seconds: float = 0.25
    jitter_seconds: float = 0.1
    pool_size: int = 4
    num_predict: int = 256
    temperature: float = 0.2
    max_response_bytes: int = 4 * 1024 * 1024

    @classmethod
    def from_env(cls, env=None) -> "OllamaConfig":
        source = os.environ if env is None else env
        def number(name, default, cast):
            raw = source.get(name, str(default))
            try:
                return cast(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} has an invalid value") from exc
        config = cls(
            endpoint=source.get("ZDX_OLLAMA_ENDPOINT", cls.endpoint),
            model=source.get("ZDX_OLLAMA_MODEL", cls.model),
            timeout_seconds=number("ZDX_OLLAMA_TIMEOUT_SECONDS", cls.timeout_seconds, float),
            max_retries=number("ZDX_OLLAMA_MAX_RETRIES", cls.max_retries, int),
            backoff_seconds=number("ZDX_OLLAMA_BACKOFF_SECONDS", cls.backoff_seconds, float),
            jitter_seconds=number("ZDX_OLLAMA_JITTER_SECONDS", cls.jitter_seconds, float),
            pool_size=number("ZDX_OLLAMA_POOL_SIZE", cls.pool_size, int),
            num_predict=number("ZDX_OLLAMA_NUM_PREDICT", cls.num_predict, int),
            temperature=number("ZDX_OLLAMA_TEMPERATURE", cls.temperature, float),
            max_response_bytes=number("ZDX_OLLAMA_MAX_RESPONSE_BYTES", cls.max_response_bytes, int),
        )
        parsed = urlsplit(config.endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("ZDX_OLLAMA_ENDPOINT must be an http(s) URL")
        if parsed.username or parsed.password:
            raise ValueError("ZDX_OLLAMA_ENDPOINT must not contain credentials")
        if parsed.scheme == "http":
            try:
                is_local = ipaddress.ip_address(parsed.hostname).is_loopback
            except ValueError:
                is_local = parsed.hostname.lower() == "localhost"
            if not is_local:
                raise ValueError("plaintext Ollama HTTP is restricted to loopback")
        if not config.model.strip():
            raise ValueError("ZDX_OLLAMA_MODEL cannot be empty")
        if config.timeout_seconds <= 0 or config.max_retries < 0:
            raise ValueError("timeout must be positive and retries non-negative")
        if config.backoff_seconds < 0 or config.jitter_seconds < 0 or config.pool_size < 1:
            raise ValueError("backoff/jitter must be non-negative and pool size positive")
        if config.num_predict < 1 or not 0 <= config.temperature <= 2 or config.max_response_bytes < 1024:
            raise ValueError("generation and response bounds are invalid")
        return config


class OllamaProvider(InferenceProvider):
    """Thread-safe pooled client for Ollama's `/api/generate` endpoint."""

    def __init__(self, config: OllamaConfig | None = None, *, sleep=time.sleep,
                 random_source: random.Random | None = None, metrics=None):
        self.config = config or OllamaConfig.from_env()
        self._sleep = sleep
        self._random = random_source or random.Random()
        self._metrics = metrics
        self._pool: queue.LifoQueue = queue.LifoQueue(self.config.pool_size)
        self._created = 0
        self._lock = threading.Lock()
        self._closed = False
        parsed = urlsplit(self.config.endpoint)
        self._scheme = parsed.scheme
        self._host = parsed.hostname
        self._port = parsed.port
        self._prefix = parsed.path.rstrip("/")

    @property
    def model(self) -> str:
        return self.config.model

    def _new_connection(self):
        cls = http.client.HTTPSConnection if self._scheme == "https" else http.client.HTTPConnection
        return cls(self._host, self._port, timeout=self.config.timeout_seconds)

    def _acquire(self):
        if self._closed:
            raise ProviderError("provider_closed", "inference provider is closed")
        try:
            return self._pool.get_nowait()
        except queue.Empty:
            with self._lock:
                if self._created < self.config.pool_size:
                    self._created += 1
                    return self._new_connection()
            try:
                return self._pool.get(timeout=self.config.timeout_seconds)
            except queue.Empty as exc:
                raise ProviderError("pool_timeout", "timed out waiting for provider connection", retryable=True) from exc

    def _release(self, connection, reusable=True):
        if not reusable or self._closed:
            try:
                connection.close()
            finally:
                with self._lock:
                    self._created = max(0, self._created - 1)
            return
        try:
            self._pool.put_nowait(connection)
        except queue.Full:
            connection.close()
            with self._lock:
                self._created = max(0, self._created - 1)

    def check_connection(self) -> dict:
        """Verify the Ollama endpoint and return its model inventory."""
        connection = self._acquire()
        reusable = True
        try:
            connection.request("GET", f"{self._prefix}/api/tags")
            response = connection.getresponse()
            body = response.read(self.config.max_response_bytes + 1)
            if len(body) > self.config.max_response_bytes:
                reusable = False
                raise ProviderError("response_too_large", "Ollama response exceeded configured limit")
            if response.status != 200:
                raise ProviderError("health_http_error", f"Ollama health check returned HTTP {response.status}", status=response.status)
            document = json.loads(body)
            if not isinstance(document, dict) or not isinstance(document.get("models"), list):
                raise ValueError("missing model inventory")
            return document
        except ProviderError:
            raise
        except (OSError, TimeoutError, http.client.HTTPException) as exc:
            reusable = False
            raise ProviderError("transport_error", f"Ollama connectivity failure: {type(exc).__name__}", retryable=True) from exc
        except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
            reusable = False
            raise ProviderError("malformed_response", "Ollama returned a malformed health response") from exc
        finally:
            self._release(connection, reusable)

    def _request(self, payload: dict, stream: bool):
        connection = self._acquire()
        reusable = True
        try:
            connection.request("POST", f"{self._prefix}/api/generate", body=json.dumps(payload),
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            body = response.read(self.config.max_response_bytes + 1)
            if len(body) > self.config.max_response_bytes:
                reusable = False
                raise ProviderError("response_too_large", "Ollama response exceeded configured limit")
            if response.status != 200:
                retryable = response.status in {408, 425, 429} or response.status >= 500
                raise ProviderError("http_error", f"Ollama returned HTTP {response.status}",
                                    retryable=retryable, status=response.status)
            if stream:
                chunks = []
                for line in body.splitlines():
                    if not line.strip():
                        continue
                    item = json.loads(line)
                    piece = item.get("response")
                    if not isinstance(piece, str):
                        raise ValueError("missing response chunk")
                    chunks.append(piece)
                return "".join(chunks), {}
            item = json.loads(body)
            text = item.get("response")
            if not isinstance(text, str):
                raise ValueError("missing response string")
            return text, item
        except ProviderError:
            raise
        except (OSError, TimeoutError, http.client.HTTPException) as exc:
            reusable = False
            raise ProviderError("transport_error", f"Ollama transport failure: {type(exc).__name__}", retryable=True) from exc
        except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
            reusable = False
            raise ProviderError("malformed_response", "Ollama returned a malformed response") from exc
        finally:
            self._release(connection, reusable)

    def _execute(self, prompt: str, system: str | None, stream: bool) -> InferenceResult:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        payload = {
            "model": self.model, "prompt": prompt, "stream": stream,
            "options": {"num_predict": self.config.num_predict, "temperature": self.config.temperature},
        }
        if system:
            payload["system"] = system
        started = time.perf_counter()
        retries = 0
        last_error = None
        for attempt in range(self.config.max_retries + 1):
            try:
                text, raw = self._request(payload, stream)
                elapsed = time.perf_counter() - started
                if self._metrics:
                    self._metrics.observe("provider_latency", elapsed)
                return InferenceResult(
                    text=text, model=str(raw.get("model", self.model)),
                    latency_seconds=elapsed,
                    prompt_tokens=raw.get("prompt_eval_count"),
                    completion_tokens=raw.get("eval_count"), retries=retries,
                )
            except ProviderError as exc:
                last_error = exc
                if not exc.retryable or attempt >= self.config.max_retries:
                    exc.attempts = attempt + 1
                    raise
                retries += 1
                if self._metrics:
                    self._metrics.increment("model_retries")
                delay = self.config.backoff_seconds * (2 ** attempt)
                delay += self._random.uniform(0, self.config.jitter_seconds)
                self._sleep(delay)
        raise last_error  # pragma: no cover

    def generate(self, prompt: str, *, system: str | None = None) -> InferenceResult:
        return self._execute(prompt, system, False)

    def stream(self, prompt: str, *, system: str | None = None) -> Iterator[str]:
        result = self._execute(prompt, system, True)
        yield result.text

    def close(self) -> None:
        self._closed = True
        while True:
            try:
                connection = self._pool.get_nowait()
            except queue.Empty:
                break
            connection.close()
        with self._lock:
            self._created = 0
