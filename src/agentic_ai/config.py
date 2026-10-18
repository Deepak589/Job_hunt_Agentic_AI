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

    # --- pipeline topology ---
    # True: ONE `agent_cv` call returns the requirement verdicts AND the first draft
    # (prompts/agent_cv.md, nodes/agent_cv.py). False: the two-call `analyze` -> `rewrite`
    # path. Either way the draft meets the same blocking `validate_facts`, the same
    # `interrupt()` review checkpoint and the same render/score tail — this flag decides
    # who writes the first draft, never what is allowed to reach a PDF.
    #
    # Two consequences worth knowing before flipping it:
    #   * The gate runs AFTER a draft exists, because one call returns both. A job the
    #     gate skips therefore costs the full drafting call (~$0.18) instead of `analyze`
    #     alone (~$0.05). That is the price of a single reasoning pass; set this False for
    #     a skip-heavy batch where most JDs never reach a draft.
    #   * Retries still go to `rewrite`, not back through `agent_cv` — re-running it would
    #     re-pay for extraction and could change the requirement list after `hard_gap_gate`
    #     had already ruled on it.
    use_agent_cv: bool = True

    # --- gate ---
    # Raised from 1 to 3: `judge_requirements` now gives every uncovered hard gap an LLM
    # second opinion first, so what reaches this count is a higher-confidence real gap,
    # not a keyword-phrasing miss — the budget can afford to be looser than the original
    # "start at 1" figure, which was tuned against the old all-keyword-miss signal.
    max_blocking_hard_gaps: int = 3

    # --- models (§12 routing) ---
    extract_model: str = "claude-haiku-4-5-20251001"  # requirement extraction, cheap + structured
    role_classifier_model: str = "claude-haiku-4-5-20251001"  # cheap, enum output only
    diagnose_model: str = "claude-sonnet-5"
    rewrite_model: str = "claude-sonnet-5"
    review_model: str = "claude-sonnet-5"
    recruiter_model: str = "claude-haiku-4-5-20251001"  # role 4: shallow, keyword-literal, fast
    hiring_manager_model: str = "claude-sonnet-5"  # role 5: defensibility judgment
    requirement_judge_model: str = "claude-sonnet-5"  # second opinion on uncovered hard/disqualifier reqs

    # --- rewrite loop ---
    # CLAUDE.md Reviewer agent: "Max 2 loops — after that, ship best version and flag
    # remaining weakness in plain text rather than looping forever." One counter shared
    # by the fact-validator retry and the review-score retry.
    max_rewrite_attempts: int = 2
    min_review_score: int = 7  # below this, retry rewrite (if attempts remain)

    # --- render overflow guard --- enforced structurally in rewrite() so a draft can't
    # overflow the one-page template in the first place, cheaper than detecting it after
    # render (ats.py's page_count gate is the belt-and-suspenders backstop for this).
    # 20, not 5: main_cv_v2 (cv_data.json) already fits all content on one page at full
    # length — profile has 17 project bullets + 4 experience bullets total (2026-10-05)
    # — so the cap only needs to stop a runaway model, not trim real profile content.
    # How far above the profile's own level a JD's language demand may sit and still reach
    # human review instead of being skipped. The CEFR scale spans 5 steps (A1..C2), so 5
    # means a language demand NEVER skips a job: it always comes back `unknown`, which both
    # `uncovered_hard` and `unmet_disqualifiers` exclude, so the posting reaches review and
    # the cover letter states the honest level instead.
    #
    # Deliberate policy, not a loose default. A stated German requirement is frequently
    # softer than it reads on Berlin postings, and a silent skip removes the choice; the
    # candidate would rather see the posting and decide. Lower it (env
    # JOBPILOT_MAX_LANGUAGE_GAP_STEPS=1) to make a demand more than one step above the
    # profile's level a skip again.
    max_language_gap_steps: int = 5

    max_bullets_per_section: int = 20
    max_bullet_chars: int = 220

    # Per-PROJECT cap, which the per-section cap above cannot express: every project lands
    # in the one "projects" section, so 20/section never bites on 17 project bullets spread
    # across 5 projects. A reader scanning a one-page CV does not read five bullets about
    # one project; the long entries buried the short ones. Experience is deliberately NOT
    # capped — there are 4 experience bullets across 2 roles, and real work history is what
    # a recruiter reads first.
    #
    # Costs something real: ats.py's "literal keywords" component (20 pts) counts evidenced
    # JD terms appearing VERBATIM in the rendered PDF, so less bullet text is less keyword
    # surface. Mitigated in the prompts, not here — both `rewrite.md` and `agent_cv.md` now
    # tell the model to pick the two bullets per project that carry the most JD-relevant
    # terms, so the trim is a selection rather than a truncation. "hard req coverage"
    # (55 pts) is unaffected: `analyze` judges requirements against the whole profile, not
    # against the draft, so trimming the draft cannot move the gate.
    #
    # Set JOBPILOT_MAX_BULLETS_PER_PROJECT=20 to restore the previous all-bullets behaviour.
    max_bullets_per_project: int = 2

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
