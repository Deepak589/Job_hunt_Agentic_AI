-- jobpilot.db — jobs + cost-ledger runs (plan.md §11, trimmed for Phase 3).
-- `requirements` and `applications` tables are Phase 5 (outcome tracking) — not built yet.

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  source TEXT,
  url TEXT,
  title TEXT,
  company TEXT,
  location TEXT,
  jd_text TEXT,
  employment_type TEXT,
  content_hash TEXT,  -- sha256(jd_text)[:16]; distinguishes a re-fetch from an edited posting
  raw_json TEXT,       -- unpopulated until step 6 sourcing connectors land; raw board payload
  first_seen TEXT,
  last_seen TEXT
);

CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  job_id TEXT REFERENCES jobs(id),
  content_hash TEXT,  -- content_hash AT RUN TIME — jobs.content_hash can move on a re-fetch
  started_at TEXT,
  finished_at TEXT,
  hard_coverage REAL,
  soft_coverage REAL,
  review_score INTEGER,
  recruiter TEXT,
  verdict TEXT,
  attempt_count INTEGER,
  skip_reason TEXT,
  tokens_in INTEGER,
  tokens_out INTEGER,
  cost_usd REAL,
  ats_total REAL
);

-- already_processed()/cost_summary() both filter on job_id; jobs.id already has its PK
-- index, but runs.job_id (a plain REFERENCES column) had none.
CREATE INDEX IF NOT EXISTS idx_runs_job_id ON runs(job_id);

-- runner.py (step 6): last-seen posting ids per (company, ats), so a poll only surfaces
-- genuinely new postings. consecutive_failures tracks a dead board without ever
-- auto-removing it — a human decides that, the runner just flags it in the digest.
CREATE TABLE IF NOT EXISTS company_snapshots (
  company TEXT,
  ats TEXT,
  posting_ids TEXT,        -- JSON array of ids seen on the last successful fetch
  fetched_at TEXT,
  consecutive_failures INTEGER DEFAULT 0,
  PRIMARY KEY (company, ats)
);
