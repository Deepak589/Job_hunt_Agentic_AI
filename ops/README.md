# Scheduling `jobpilot run`

Not installed automatically by anything in this repo — pick one, wire it up yourself.

## Option A — launchd (macOS)

1. Copy `com.jobpilot.run.plist.template` to `~/Library/LaunchAgents/com.jobpilot.run.plist`.
2. Replace `REPO_PATH` with this repo's absolute path, and `PYTHON_BIN` with the
   interpreter that has this project's deps installed (e.g. `REPO_PATH/.venv/bin/python`).
3. `launchctl load ~/Library/LaunchAgents/com.jobpilot.run.plist`

Runs hourly (`StartInterval` = 3600s). `launchctl unload` the same path to stop it.

## Option B — cron

```
0 * * * * cd /path/to/Agentic_ai && /path/to/venv/bin/jobpilot run --digest json >> ~/jobpilot-digest.log 2>&1
```

Either way: `jobpilot run --digest json` is the whole job — it polls `config/companies.yaml`,
runs new postings through the pipeline, and prints one JSON digest line. Check
`~/jobpilot-digest.log` (or wherever you pointed stdout) for `notify` lines — those flag
a company with 3+ consecutive fetch failures.
