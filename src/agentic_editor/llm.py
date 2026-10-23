"""Client for models served by Ollama through its OpenAI-compatible API."""

import base64
import json
import logging
import time
from pathlib import Path

import httpx
from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


def parse[M: BaseModel](model: type[M], reply: object) -> M:
    """A reply as `model`. A reply outside the schema's limits (a score of 11, a box with
    three corners) raises LLMError like any other bad answer, so callers' fallbacks apply."""
    try:
        return model.model_validate(reply)
    except ValidationError as exc:
        raise LLMError(f"reply doesn't fit {model.__name__}: {exc.errors()[0]['msg']}") from exc


class OllamaClient:
    """A model served by Ollama. Used as a context manager, it holds `slot` (the GPU)
    while open and unloads the model when closed."""

    def __init__(self, base_url: str, model: str, timeout: float = 600, slot=None, progress=None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.http = httpx.Client(timeout=timeout)
        self.last_tokens_per_second: float | None = None
        self.slot = slot
        self.progress = progress

    def __enter__(self) -> "OllamaClient":
        if self.slot is not None:
            self.slot.acquire(f"run {self.model}", progress=self.progress)
        return self

    def __exit__(self, *exc) -> None:
        try:
            self.unload()
            self.http.close()
        finally:
            if self.slot is not None:
                self.slot.release()

    def chat_json(
        self,
        messages: list[dict],
        schema: dict,
        *,
        images: list[Path] | None = None,
        temperature: float = 0.3,
    ) -> dict:
        """Ask for a reply constrained to a JSON schema and return it parsed."""
        if images:
            messages = [*messages[:-1], _with_images(messages[-1], images)]
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "reply", "schema": schema, "strict": True},
            },
            # Planning needs short, structured answers; the hidden reasoning pass only
            # costs time and VRAM on the small models.
            "reasoning_effort": "none",
        }
        # Small models now and then stop mid-answer; one more try nearly always lands.
        for attempt in range(2):
            started = time.monotonic()
            try:
                response = self.http.post(f"{self.base_url}/v1/chat/completions", json=body)
            except httpx.HTTPError as exc:
                # A timeout or a restarted server is one more failed answer to fall back from.
                raise LLMError(f"{self.model} could not be reached: {exc!r}") from exc
            if response.status_code != 200:
                raise LLMError(
                    f"{self.model} returned {response.status_code}: {response.text[:500]}"
                )
            try:
                data = response.json()
                content = data["choices"][0]["message"].get("content") or ""
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                raise LLMError(
                    f"{self.model} sent an unexpected reply: {response.text[:300]}"
                ) from exc
            elapsed = time.monotonic() - started
            tokens = (data.get("usage") or {}).get("completion_tokens", 0)
            self.last_tokens_per_second = tokens / elapsed if elapsed else None
            log.debug("%s: %d tokens in %.1fs", self.model, tokens, elapsed)
            try:
                return json.loads(content)
            except json.JSONDecodeError as exc:
                if attempt:
                    raise LLMError(f"{self.model} did not return JSON: {content[:300]}") from exc
                log.info("%s returned broken JSON, asking again", self.model)
        raise AssertionError("unreachable")

    def unload(self) -> None:
        """Evict the model from GPU memory now instead of after Ollama's idle timeout."""
        try:
            self.http.post(
                f"{self.base_url}/api/generate", json={"model": self.model, "keep_alive": 0}
            )
        except httpx.HTTPError as exc:
            log.warning("could not unload %s: %s", self.model, exc)


def _with_images(message: dict, images: list[Path]) -> dict:
    parts = [{"type": "text", "text": message["content"]}]
    for path in images:
        encoded = base64.b64encode(path.read_bytes()).decode()
        parts.append(
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}}
        )
    return {**message, "content": parts}
