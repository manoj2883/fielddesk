"""Runtime settings, overridable via `FIELDDESK_`-prefixed env vars or `.env`."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class ModelPrice(BaseModel):
    """USD per million tokens. Cache writes use the 5-minute TTL rate."""

    input: float
    output: float
    cache_read: float
    cache_write: float


# Source: platform.claude.com/docs/en/about-claude/pricing, read 2026-10-20.
# Haiku 5.5 rates are the <=100K-token prompt tier; FieldDesk prompts are far below that.
DEFAULT_PRICES: dict[str, ModelPrice] = {
    "claude-haiku-5-5": ModelPrice(input=0.10, output=0.50, cache_read=0.01, cache_write=0.125),
    "claude-sonnet-5-5": ModelPrice(input=2.00, output=10.00, cache_read=0.10, cache_write=2.50),
    "claude-opus-5-5": ModelPrice(input=4.00, output=20.00, cache_read=0.20, cache_write=5.00),
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FIELDDESK_",
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("ANTHROPIC_API_KEY", "FIELDDESK_ANTHROPIC_API_KEY"),
    )
    openrouter_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENROUTER_API_KEY", "FIELDDESK_OPENROUTER_API_KEY"),
    )

    # Model IDs choose the provider: "vendor/model" -> OpenRouter, bare "claude-*" -> Anthropic.
    # Defaults are free OpenRouter models (plan change 2026-10-20). To switch back to Claude, set
    # FIELDDESK_CLASSIFY_MODEL=claude-haiku-5-5, FIELDDESK_DRAFT_MODEL=claude-sonnet-5-5 and
    # FIELDDESK_GENERATOR_MODEL=claude-sonnet-5-5.
    # Chosen 2026-10-21 by live smoke tests (JOURNAL): every pair that must be independent comes
    # from different families (generator vs classifier, generator vs validator, classifier vs
    # validator).
    classify_model: str = "nvidia/nemotron-3.5-super-vl:free"
    draft_model: str = "minimax/minimax-m3.1-flash:free"
    generator_model: str = "minimax/minimax-m3.1-flash:free"
    validator_model: str = "google/gemma-4-31b-it:free"
    validator_fallback_model: str = "dots-studio/dots-3-note-preview:free"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Free tier allows ~20 requests/minute; 3.2 s spacing stays under it.
    openrouter_min_interval_s: float = 3.2
    # Models whose OpenRouter endpoints support response_format=json_schema (checked 2026-10-20
    # from the public /models list). Others use JSON mode plus the schema in the prompt.
    openrouter_json_schema_models: frozenset[str] = frozenset(
        {
            "nvidia/nemotron-3-super-120b-a12b:free",
            "nvidia/nemotron-3.5-super-vl:free",
            "dots-studio/dots-3-note-preview:free",
            "xingchen-agi/xing4.0-29b-a4b:free",
        }
    )

    classify_prompt_version: str = "classify_v1"
    draft_prompt_version: str = "draft_v1"

    confidence_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    max_spend_usd: float = Field(default=5.0, gt=0.0)

    # Generous: some free models reason before answering and are cut off at low limits. Free
    # models cost $0 per token, so the budget reservation for these limits is also $0.
    classify_max_tokens: int = 2048
    draft_max_tokens: int = 2048
    generator_max_tokens: int = 6000
    generator_timeout_s: float = 120.0
    node_run_timeout_s: float = 45.0
    retry_max_attempts: int = 3
    retry_initial_interval_s: float = 0.5

    ledger_path: Path = REPO_ROOT / ".spend" / "ledger.jsonl"
    db_path: Path = REPO_ROOT / "fielddesk.db"
    data_dir: Path = REPO_ROOT / "data"
    results_dir: Path = REPO_ROOT / "evals" / "results"

    seed: int = 1729
    dedup_threshold: float = Field(default=90.0, ge=0.0, le=100.0)

    prices: dict[str, ModelPrice] = Field(default_factory=lambda: dict(DEFAULT_PRICES))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
