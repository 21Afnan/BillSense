"""Runtime configuration loaded from environment variables (.env in the project root)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

SOURCE_DIR = Path(__file__).resolve().parents[3]   # .../source
PROJECT_DIR = SOURCE_DIR.parent                     # holds .env, output/, bills/, test/
load_dotenv(PROJECT_DIR / ".env")                   # real keys stay outside source/ (never submitted)
load_dotenv(SOURCE_DIR / ".env")                    # optional fallback; does not override

# Only these models are allowed by the competition rules. Anything else is rejected
# at startup so a typo can never lead to disqualification.
ALLOWED_GEMINI = {"gemini-3.5-flash-lite", "gemini-3.1-flash-lite"}
ALLOWED_CLAUDE = {"claude-haiku-4-5"}
PLACEHOLDERS = {"", "your-api-key-here", "your-gemini-key-here", "your-anthropic-key-here"}


@dataclass(frozen=True)
class Settings:
    model_provider: str        # "google" (primary)
    model_name: str            # Gemini model (primary)
    api_key: str               # Gemini key 1
    backup_api_key: str        # Gemini key 2 (used only if key 1 is rejected or Google is failing)
    fallback_provider: str     # "anthropic"
    fallback_model: str        # Claude model used when Gemini is unavailable
    anthropic_api_key: str
    requests_per_minute: int
    max_retries: int
    timeout_seconds: int
    cache_dir: Path

    def validate(self) -> None:
        if self.model_provider != "google" or self.fallback_provider != "anthropic":
            raise RuntimeError("Supported providers: MODEL_PROVIDER=google, FALLBACK_MODEL_PROVIDER=anthropic")
        if self.model_name not in ALLOWED_GEMINI:
            raise RuntimeError(f"MODEL_NAME '{self.model_name}' is not allowed. Use one of {sorted(ALLOWED_GEMINI)}")
        if self.fallback_model not in ALLOWED_CLAUDE:
            raise RuntimeError(f"FALLBACK_MODEL_NAME '{self.fallback_model}' is not allowed. Use {sorted(ALLOWED_CLAUDE)}")
        if not (self.api_key or self.backup_api_key or self.anthropic_api_key):
            raise RuntimeError("No API key set. Copy .env.example to .env and add API_KEY and/or ANTHROPIC_API_KEY.")


def _key(*names: str) -> str:
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value not in PLACEHOLDERS:
            return value
    return ""


def get_settings() -> Settings:
    primary = _key("API_KEY", "GEMINI_API_KEY")
    backup = _key("GEMINI_API_KEY_2", "GEMINI_API_KEY2", "GEMINI_API_KEY")
    return Settings(
        model_provider=os.getenv("MODEL_PROVIDER", "google").strip().lower(),
        model_name=os.getenv("MODEL_NAME", "gemini-3.5-flash-lite").strip(),
        api_key=primary,
        backup_api_key=backup if backup != primary else "",
        fallback_provider=os.getenv("FALLBACK_MODEL_PROVIDER", "anthropic").strip().lower(),
        fallback_model=os.getenv("FALLBACK_MODEL_NAME", "claude-haiku-4-5").strip(),
        anthropic_api_key=_key("ANTHROPIC_API_KEY", "CLAUDE_API_KEY"),
        requests_per_minute=int(os.getenv("REQUESTS_PER_MINUTE", "12")),
        max_retries=int(os.getenv("MAX_RETRIES", "2")),
        timeout_seconds=int(os.getenv("REQUEST_TIMEOUT_SECONDS", "90")),
        cache_dir=Path(os.getenv("CACHE_DIR", str(PROJECT_DIR / ".cache"))),
    )
