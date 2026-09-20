"""Daily spend cap (solution.md step 1). Two call sites:

- `check_budget()` — a plain gate, used by the single-job CLI path (`jobpilot add
  --file`). Raises `BudgetExceeded` if today's PERSISTED spend already hit the cap.
- `BudgetGuard` — used by `graph.run_many` (the concurrent `--dir` path), where
  `check_budget()` alone is a race: N jobs can all pass the same persisted-spend
  check before any of them persists a run, and all N run over cap. `BudgetGuard`
  reserves each job's estimated cost against the cap *before* it starts, under an
  asyncio.Lock shared by every concurrent job in the same run_many() call, and
  replaces the estimate with the real cost once the job finishes.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from .config import settings


class BudgetExceeded(Exception):
    """Today's persisted spend has already hit (or would exceed) the daily cap."""

    def __init__(self, message: str, spent: float) -> None:
        super().__init__(message)
        self.spent = spent


def _today_start() -> datetime:
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def spent_today() -> float:
    from .db.repo import cost_summary

    return cost_summary(since=_today_start())["total_cost_usd"]


def check_budget() -> None:
    """Raise BudgetExceeded if today's persisted spend already hit the cap.
    No-op when `Settings.max_daily_cost_usd` is None (no cap configured)."""
    if settings.max_daily_cost_usd is None:
        return
    spent = spent_today()
    if spent >= settings.max_daily_cost_usd:
        raise BudgetExceeded(
            f"spent ${spent:.4f} today, cap is ${settings.max_daily_cost_usd:.4f}", spent=spent
        )


def estimated_job_cost() -> float:
    """Rolling average cost of a past run, as the estimate for one not-yet-run job.
    No history yet -> a small flat guess rather than 0 (0 would let every job through
    the reservation check regardless of the cap)."""
    from .db.repo import cost_summary

    summary = cost_summary()
    if summary["total_runs"]:
        return summary["total_cost_usd"] / summary["total_runs"]
    return 0.05


class BudgetGuard:
    """Reserves in-flight spend against the daily cap for one run_many() batch.

    `persisted` is a snapshot of today's spend taken once, at batch start — runs
    finishing mid-batch are tracked via `reserved`, not by re-querying the db, so
    concurrent jobs in the same batch never race the persisted-spend check.
    """

    def __init__(self, cap: float | None, persisted: float) -> None:
        self.cap = cap
        self.persisted = persisted
        self.reserved = 0.0
        self._lock = asyncio.Lock()

    async def try_reserve(self, est_cost: float) -> bool:
        async with self._lock:
            if self.cap is not None and self.persisted + self.reserved + est_cost > self.cap:
                return False
            self.reserved += est_cost
            return True

    async def settle(self, est_cost: float, actual_cost: float) -> None:
        """Replace a job's reservation with its real cost once it finishes."""
        async with self._lock:
            self.reserved += actual_cost - est_cost

    async def release(self, est_cost: float) -> None:
        """Drop a reservation for a job that never ran (skipped, rejected)."""
        async with self._lock:
            self.reserved -= est_cost

    @classmethod
    def for_today(cls) -> BudgetGuard:
        return cls(cap=settings.max_daily_cost_usd, persisted=spent_today())
