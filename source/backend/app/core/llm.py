"""LLM client with an ordered fallback chain, retries, JSON parsing and an on-disk cache.

Chain (only competition-approved models):
  1. Gemini, primary key
  2. Gemini, backup key   - used when key 1 is rejected (401/403) or Google is failing (5xx/network);
                            NOT used to get around a rate limit or quota (429)
  3. Claude Haiku 4.5     - used when Gemini is rate-limited/unavailable and ANTHROPIC_API_KEY is set
Every answer records which model produced it (stats["by_model"]).
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import re
import threading
import time
from pathlib import Path

from PIL import Image

from app.core.cache import cache_key, read_json, write_json_atomic
from app.core.config import Settings

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class ProviderError(Exception):
    """kind: 'auth' (bad key), 'quota' (rate limit / quota), 'transient' (5xx, network, bad JSON), 'fatal'."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def _retry_delay(err: Exception, attempt: int) -> float:
    match = re.search(r"retry(?:Delay)?['\"]?\s*(?:in|:)?\s*['\"]?(\d+(?:\.\d+)?)s", str(err), re.I)
    return float(match.group(1)) + 1.0 if match else min(2 ** attempt * 2.0, 30.0)


# ============================================================== providers
class GeminiProvider:
    def __init__(self, api_key: str, model: str, settings: Settings, label: str):
        from google import genai
        from google.genai import types

        self.types = types
        self.name = f"{model} ({label})"
        self.model = model
        self.client = genai.Client(api_key=api_key,
                                   http_options=types.HttpOptions(timeout=settings.timeout_seconds * 1000))
        self._min_interval = 60.0 / max(settings.requests_per_minute, 1)
        self._last_call = 0.0
        self._lock = threading.Lock()

    def _throttle(self) -> None:
        with self._lock:
            wait = self._last_call + self._min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()

    def generate(self, system: str, prompt: str, image: tuple[bytes, str] | None) -> tuple[str, int, int]:
        from google.genai import errors

        t = self.types
        contents: list = ([t.Part.from_bytes(data=image[0], mime_type=image[1])] if image else []) + [prompt]
        config = t.GenerateContentConfig(
            system_instruction=system, temperature=0.0, response_mime_type="application/json",
            automatic_function_calling=t.AutomaticFunctionCallingConfig(disable=True),
        )
        self._throttle()
        try:
            resp = self.client.models.generate_content(model=self.model, contents=contents, config=config)
        except errors.APIError as err:
            code = getattr(err, "code", None)
            kind = {401: "auth", 403: "auth", 429: "quota"}.get(code) or (
                "transient" if code and code >= 500 else "fatal")
            raise ProviderError(kind, f"Gemini {code}: {err}") from err
        except Exception as err:  # network / timeout
            raise ProviderError("transient", f"Gemini request failed: {err}") from err
        usage = resp.usage_metadata
        return resp.text or "", (usage.prompt_token_count or 0) if usage else 0, \
            (usage.candidates_token_count or 0) if usage else 0


class ClaudeProvider:
    MAX_SIDE = 1568  # Claude's recommended max image edge; keeps the base64 payload well under 5 MB

    def __init__(self, api_key: str, model: str, settings: Settings):
        import anthropic

        self.anthropic = anthropic
        self.name = model
        self.model = model
        self.client = anthropic.Anthropic(api_key=api_key, timeout=float(settings.timeout_seconds), max_retries=0)

    def _image_block(self, image: tuple[bytes, str]) -> dict:
        img = Image.open(io.BytesIO(image[0])).convert("RGB")
        img.thumbnail((self.MAX_SIDE, self.MAX_SIDE), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=92)
        data = base64.standard_b64encode(buf.getvalue()).decode("utf-8")
        return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}

    def generate(self, system: str, prompt: str, image: tuple[bytes, str] | None) -> tuple[str, int, int]:
        a = self.anthropic
        content = ([self._image_block(image)] if image else []) + [
            {"type": "text", "text": prompt + "\n\nRespond with the JSON object only."}]
        try:
            resp = self.client.messages.create(model=self.model, max_tokens=8000, system=system,
                                               messages=[{"role": "user", "content": content}])
        except (a.AuthenticationError, a.PermissionDeniedError) as err:
            raise ProviderError("auth", f"Claude: {err}") from err
        except a.RateLimitError as err:
            raise ProviderError("quota", f"Claude rate limit: {err}") from err
        except a.APIStatusError as err:
            raise ProviderError("transient" if err.status_code >= 500 else "fatal", f"Claude: {err}") from err
        except a.APIConnectionError as err:  # includes timeouts
            raise ProviderError("transient", f"Claude connection: {err}") from err
        if resp.stop_reason == "refusal":
            raise ProviderError("fatal", "Claude declined the request")
        text = "".join(b.text for b in resp.content if b.type == "text")
        return text, resp.usage.input_tokens, resp.usage.output_tokens


# ============================================================== client
class LLMClient:
    def __init__(self, settings: Settings):
        settings.validate()
        self.settings = settings
        self.providers: list = []
        if settings.api_key:
            self.providers.append(GeminiProvider(settings.api_key, settings.model_name, settings, "key 1"))
        if settings.backup_api_key:
            self.providers.append(GeminiProvider(settings.backup_api_key, settings.model_name, settings, "key 2"))
        if settings.anthropic_api_key:
            self.providers.append(ClaudeProvider(settings.anthropic_api_key, settings.fallback_model, settings))
        if not self.providers:
            raise RuntimeError("No LLM API key configured. Set API_KEY (Gemini) and/or ANTHROPIC_API_KEY in .env.")
        self.stats = {"live_requests": 0, "cache_hits": 0, "input_tokens": 0, "output_tokens": 0, "by_model": {}}
        self.cache_dir = settings.cache_dir / "llm"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, system: str, prompt: str, image: tuple[bytes, str] | None) -> Path:
        """Key = schema version + primary provider/model + prompt version + both prompts + image hash."""
        from app.services.prompts import PROMPT_VERSION

        image_hash = hashlib.sha256(image[0]).hexdigest() if image else "no-image"
        key = cache_key("google", self.settings.model_name, PROMPT_VERSION, system, prompt, image_hash)
        return self.cache_dir / f"{key}.json"

    def _call(self, provider, system: str, prompt: str, image) -> dict:
        """One provider with bounded retries for transient errors and invalid JSON."""
        last: Exception | None = None
        for attempt in range(self.settings.max_retries + 1):
            try:
                text, tin, tout = provider.generate(system, prompt, image)
                self.stats["live_requests"] += 1
                self.stats["input_tokens"] += tin
                self.stats["output_tokens"] += tout
                return parse_json(text)
            except ValueError as err:  # model returned invalid JSON
                last = err
                log.warning("%s returned invalid JSON (attempt %d)", provider.name, attempt + 1)
            except ProviderError as err:
                if err.kind != "transient":
                    raise
                last = err
                delay = _retry_delay(err, attempt)
                log.warning("%s: %s (attempt %d), retrying in %.0fs", provider.name, err, attempt + 1, delay)
                time.sleep(delay)
        raise ProviderError("transient", f"{provider.name} failed after retries: {last}")

    def generate_json(self, system: str, prompt: str, image: tuple[bytes, str] | None = None,
                      use_cache: bool = True) -> dict:
        """Return parsed JSON from the first provider in the chain that succeeds."""
        cache_file = self._cache_path(system, prompt, image)
        cached = read_json(cache_file) if use_cache else None
        if isinstance(cached, dict) and isinstance(cached.get("data"), dict):
            self.stats["cache_hits"] += 1
            log.info("cache hit (no API call; originally answered by %s)", cached.get("model"))
            return cached["data"]

        errors: list[str] = []
        quota_hit = False
        for provider in self.providers:
            is_gemini_backup = isinstance(provider, GeminiProvider) and provider.name.endswith("(key 2)")
            if is_gemini_backup and quota_hit:
                continue  # never switch keys to get around a Gemini rate limit / quota
            try:
                data = self._call(provider, system, prompt, image)
            except ProviderError as err:
                errors.append(f"{provider.name}: {err.kind}")
                quota_hit = quota_hit or (err.kind == "quota" and isinstance(provider, GeminiProvider))
                log.warning("%s unavailable (%s); trying next provider", provider.name, err.kind)
                continue
            self.stats["by_model"][provider.name] = self.stats["by_model"].get(provider.name, 0) + 1
            write_json_atomic(cache_file, {"model": provider.name, "data": data})
            return data
        raise LLMError("All LLM providers failed: " + "; ".join(errors))


def parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object in response")
        data = json.loads(text[start: end + 1])
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
        data = data[0]
    if not isinstance(data, dict):
        raise ValueError("response JSON is not an object")
    return data
