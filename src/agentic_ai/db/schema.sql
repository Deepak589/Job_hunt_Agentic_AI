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
  first_seen TEXT,
  last_seen TEXT
);

CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  job_id TEXT REFERENCES jobs(id),
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
