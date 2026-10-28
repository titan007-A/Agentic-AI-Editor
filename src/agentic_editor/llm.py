"""Client for models served by Ollama through its own chat API."""

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

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float = 600,
        slot=None,
        progress=None,
        gpu_layers: int | None = None,
        context: int | None = None,
        max_reply: int | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        # Tokens of prompt (images included) and reply the model holds at once, None for
        # Ollama's default; and the most a reply may run to, so one that loops ends.
        self.context = context
        self.max_reply = max_reply
        # Layers to put on the GPU; None leaves it to Ollama, which keeps a margin and so
        # can split a model that would fit across the CPU too, at about two thirds the speed.
        self.gpu_layers = gpu_layers
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
            "format": schema,
            "stream": False,
            # Planning needs short, structured answers; the hidden reasoning pass only
            # costs time and VRAM on the small models.
            "think": False,
            "options": {"temperature": temperature},
        }
        if self.context:
            body["options"]["num_ctx"] = self.context
        if self.max_reply:
            body["options"]["num_predict"] = self.max_reply
        # Small models now and then stop mid-answer; one more try nearly always lands.
        for attempt in range(2):
            if self.gpu_layers is not None:
                body["options"]["num_gpu"] = self.gpu_layers
            started = time.monotonic()
            try:
                response = self.http.post(f"{self.base_url}/api/chat", json=body)
            except httpx.HTTPError as exc:
                # A timeout or a restarted server is one more failed answer to fall back from.
                raise LLMError(f"{self.model} could not be reached: {exc!r}") from exc
            if response.status_code != 200 and self.gpu_layers is not None:
                # Something else holds GPU memory right now: let Ollama place it instead.
                log.warning(
                    "%s didn't load with %d GPU layers (%s); letting Ollama decide",
                    self.model, self.gpu_layers, response.text[:200],
                )  # fmt: skip
                self.gpu_layers = None
                body["options"].pop("num_gpu", None)
                response = self.http.post(f"{self.base_url}/api/chat", json=body)
            if "structured output is unavailable" in response.text:
                raise LLMError(
                    f"{self.model} can't answer to a JSON schema (an MLX build); use a GGUF "
                    "build such as qwen3.5:9b-q4_K_M"
                )
            if response.status_code != 200:
                raise LLMError(
                    f"{self.model} returned {response.status_code}: {response.text[:500]}"
                )
            try:
                data = response.json()
                content = data["message"].get("content") or ""
            except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
                raise LLMError(
                    f"{self.model} sent an unexpected reply: {response.text[:300]}"
                ) from exc
            elapsed = time.monotonic() - started
            tokens = data.get("eval_count", 0)
            self.last_tokens_per_second = tokens / elapsed if elapsed else None
            log.debug("%s: %d tokens in %.1fs", self.model, tokens, elapsed)
            try:
                return json.loads(content)
            except json.JSONDecodeError as exc:
                # An answer cut off at the length cap comes out the same way when asked again.
                if attempt or data.get("done_reason") == "length":
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
    return {**message, "images": [base64.b64encode(p.read_bytes()).decode() for p in images]}
