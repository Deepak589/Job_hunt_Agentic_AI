"""Settings — thresholds, model ids, paths. Override any field via .env or environment."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]

# ANTHROPIC_API_KEY is read from the environment by the Anthropic SDK, not by Settings.
load_dotenv(ROOT / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="JOBPILOT_", extra="ignore")

    # --- paths ---
    profile_path: Path = ROOT / "data" / "master_profile.yaml"
    chroma_path: Path = ROOT / "data" / "chroma"
    prompts_dir: Path = ROOT / "prompts"
    collection_name: str = "evidence"
    checkpoint_db_path: Path = ROOT / "data" / "checkpoints.db"  # LangGraph's own format
    jobs_db_path: Path = ROOT / "data" / "jobpilot.db"  # our jobs/runs cost ledger, §11
    log_path: Path = ROOT / "data" / "jobpilot.log.jsonl"  # structured JSONL run log

    # --- budget ---
    max_daily_cost_usd: float | None = None  # None = no cap; set via JOBPILOT_MAX_DAILY_COST_USD

    # --- retrieval ---
    embedding_model: str = "BAAI/bge-m3"
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    top_k: int = 5

    # CALIBRATED, not guessed. bge-m3 cosine similarities sit high even for unrelated
    # text, so a round number is wrong in an unknown direction.
    #
    # Derivation: `jobpilot index calibrate` scores the 15 hand-labeled probes in
    # evidence.PROBES against the real index and picks the max-margin split.
    # Measured 2026-09-14 — bge-m3, 20 bullet chunks, cosine:
    #   covered probes    0.5581 .. 0.6651  (lowest: "CI/CD pipelines with GitHub Actions")
    #   uncovered probes  0.4192 .. 0.5536  (highest: "4+ years of professional backend")
    #   separating gap    0.0045 -> midpoint 0.5558, 0/15 probes misclassified
    #
    # ponytail: that margin is 0.0045 wide, so the semantic signal is near its useful
    # limit. It collapsed from 0.0328 once real JD requirement text was added as probes —
    # live postings are longer and more contextual than a bare skill phrase, and
    # "Production experience with Apache Kafka" scored 0.5462 against a Docker/AWS bullet
    # with no Kafka anywhere in the profile. Named technologies are therefore carried by
    # the KEYWORD half of §6, not this one; treat semantic as the paraphrase catcher only.
    # Upgrade path when a real JD is misjudged: rerank the top-k with a cross-encoder
    # before thresholding, rather than pushing this number around.
    #
    # Implemented (evidence.rerank): the cross-encoder reorders top-k candidates and sets
    # Evidence.rerank_score, but this threshold still compares against `similarity`
    # (cosine), not rerank_score. Reasoning: this file's own note above says named
    # technologies are carried by the KEYWORD half of §6, not semantic — a cross-encoder
    # still doesn't know your CV lacks Kafka, it just reads pairs more carefully. `jobpilot
    # index calibrate` shows rerank does not beat cosine on the probe set (see its output),
    # so there's no evidence yet to threshold on it. Revisit if calibrate ever shows
    # otherwise.
    sem_threshold: float = 0.5558

    # --- gate ---
    # §18.4: start at 1, tune once there are ~30 runs with real outcomes.
    max_blocking_hard_gaps: int = 1

    # --- models (§12 routing) ---
    extract_model: str = "claude-haiku-4-5-20251001"  # requirement extraction, cheap + structured
    role_classifier_model: str = "claude-haiku-4-5-20251001"  # cheap, enum output only
    diagnose_model: str = "claude-sonnet-5"
    rewrite_model: str = "claude-sonnet-5"
    review_model: str = "claude-sonnet-5"
    recruiter_model: str = "claude-haiku-4-5-20251001"  # role 4: shallow, keyword-literal, fast
    hiring_manager_model: str = "claude-sonnet-5"  # role 5: defensibility judgment

    # --- rewrite loop ---
    # CLAUDE.md Reviewer agent: "Max 2 loops — after that, ship best version and flag
    # remaining weakness in plain text rather than looping forever." One counter shared
    # by the fact-validator retry and the review-score retry.
    max_rewrite_attempts: int = 2
    min_review_score: int = 7  # below this, retry rewrite (if attempts remain)

    # --- tracing (optional) ---
    # Unset by default -> tracing is a no-op. Set all three to enable Langfuse.
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str | None = None

    # --- transport (429/5xx backoff, shared by all 6 ChatAnthropic nodes) ---
    llm_max_retries: int = 3

    # --- concurrency (jobpilot add --dir) ---
    max_concurrent_jobs: int = 3


settings = Settings()
