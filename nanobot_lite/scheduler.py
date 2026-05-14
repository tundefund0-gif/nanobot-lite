"""Lightweight in-process scheduler (no cron daemon needed)."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Callable

try:
    from loguru import logger
except ImportError:
    import sys as _sys
    class _Dummy:
        def debug(self, *a, **k): pass
        def info(self, *a, **k): print(*a, file=_sys.stderr)
        def warning(self, *a, **k): print(*a, file=_sys.stderr)
        def error(self, *a, **k): print(*a, file=_sys.stderr)
        def success(self, *a, **k): pass
    logger = _Dummy()


# ─── Job dataclass ─────────────────────────────────────────────────────────────

@dataclass
class Job:
    name: str
    func: Callable[..., Any]
    interval_seconds: float
    args: tuple = field(default_factory=())
    kwargs: dict[str, Any] = field(default_factory=dict)
    last_run: float = 0.0
    run_count: int = 0
    enabled: bool = True
    one_shot: bool = False

    @property
    def next_run(self) -> float:
        if self.last_run == 0:
            return 0.0  # run immediately on first tick
        return self.last_run + self.interval_seconds

    def due(self, now: float) -> bool:
        return now >= self.next_run and self.enabled


# ─── Scheduler ────────────────────────────────────────────────────────────────

class Scheduler:
    """
    In-process scheduler for recurring tasks inside the agent loop.
    No cron daemon needed — call tick() from your event loop.

    Usage:
        scheduler = Scheduler(bus)
        scheduler.every(300, "health_check", health_check_job)
        scheduler.every_minute("log_stats", log_stats_job)

        # In your async loop:
        async def run():
            while True:
                await scheduler.tick()
                await asyncio.sleep(10)
    """

    def __init__(self, bus: Any | None = None):
        self._jobs: dict[str, Job] = {}
        self._lock = Lock()
        self._start_time = time.time()
        self.bus = bus  # optional MessageBus for enqueuing

    # ── Registration ───────────────────────────────────────────────────────────

    def every(
        self,
        interval_seconds: float,
        name: str,
        func: Callable[..., Any],
        *args,
        one_shot: bool = False,
        **kwargs,
    ) -> Job:
        """Register a job to run every `interval_seconds` seconds."""
        with self._lock:
            job = Job(
                name=name,
                func=func,
                interval_seconds=interval_seconds,
                args=args,
                kwargs=kwargs,
                one_shot=one_shot,
            )
            self._jobs[name] = job
            logger.info(f"Scheduled job: {name} (every {interval_seconds}s)")
            return job

    def every_minute(self, name: str, func: Callable[..., Any], *args, **kwargs) -> Job:
        return self.every(60, name, func, *args, **kwargs)

    def every_hour(self, name: str, func: Callable[..., Any], *args, **kwargs) -> Job:
        return self.every(3600, name, func, *args, **kwargs)

    def every_day(self, name: str, func: Callable[..., Any], *args, **kwargs) -> Job:
        return self.every(86400, name, func, *args, **kwargs)

    def cancel(self, name: str) -> bool:
        """Cancel a job by name."""
        with self._lock:
            if name in self._jobs:
                del self._jobs[name]
                logger.info(f"Cancelled job: {name}")
                return True
            return False

    def pause(self, name: str) -> bool:
        with self._lock:
            if name in self._jobs:
                self._jobs[name].enabled = False
                return True
            return False

    def resume(self, name: str) -> bool:
        with self._lock:
            if name in self._jobs:
                self._jobs[name].enabled = True
                return True
            return False

    # ── Tick ───────────────────────────────────────────────────────────────────

    async def tick(self) -> list[tuple[str, Any, bool]]:
        """
        Call this from your async event loop (e.g. every 10s).
        Returns list of (job_name, result, success) for all jobs that fired.
        """
        results: list[tuple[str, Any, bool]] = []
        now = time.time()

        with self._lock:
            jobs = list(self._jobs.values())

        for job in jobs:
            if not job.due(now):
                continue

            try:
                if asyncio.iscoroutinefunction(job.func):
                    result = await job.func(*job.args, **job.kwargs)
                else:
                    result = job.func(*job.args, **job.kwargs)

                job.last_run = now
                job.run_count += 1
                results.append((job.name, result, True))
                logger.debug(f"Job fired: {job.name} (run #{job.run_count})")

            except Exception as e:
                results.append((job.name, str(e), False))
                logger.error(f"Job failed: {job.name}: {e}")

            # Auto-cancel one-shot jobs
            if job.one_shot:
                with self._lock:
                    self._jobs.pop(job.name, None)

        return results

    # ── Query ─────────────────────────────────────────────────────────────────

    def list_jobs(self) -> list[dict[str, Any]]:
        """Return list of job status dicts."""
        with self._lock:
            jobs = list(self._jobs.values())
        now = time.time()
        return [
            {
                "name": j.name,
                "interval_s": j.interval_seconds,
                "next_run_s": max(0, j.next_run - now),
                "last_run_ago_s": now - j.last_run if j.last_run else None,
                "run_count": j.run_count,
                "enabled": j.enabled,
                "one_shot": j.one_shot,
            }
            for j in jobs
        ]

    @property
    def uptime_s(self) -> float:
        return time.time() - self._start_time


# ─── Built-in jobs ─────────────────────────────────────────────────────────────

try:
    import psutil
    _HAS_PSUTIL = True
except Exception:
    _HAS_PSUTIL = False


def health_check_job() -> str:
    """Built-in: log system stats every 5 minutes."""
    if not _HAS_PSUTIL:
        return "psutil not available — skipped"
    import psutil, platform
    cpu = psutil.cpu_percent(interval=0.1)
    mem = psutil.virtual_memory().percent
    disk = psutil.disk_usage("/").percent
    logger.info(f"health_check: CPU={cpu:.0f}% MEM={mem:.0f}% DISK={disk:.0f}%")
    return f"CPU={cpu:.0f}% MEM={mem:.0f}% DISK={disk:.0f}%"


def session_cleanup_job(config_dir: str = "~/.nanobot_lite") -> int:
    """Built-in: remove sessions older than 30 days."""
    from nanobot_lite.agent.memory import SessionStore
    import time
    config_dir = os.path.expanduser(config_dir)
    store = SessionStore(config_dir)
    cutoff = time.time() - 30 * 86400
    removed = 0
    # Simple: count sessions, filter by updated_at
    try:
        files = list(Path(os.path.join(config_dir, "sessions")).glob("*.json"))
        for f in files:
            try:
                import json
                data = json.loads(f.read_text())
                updated = data.get("updated_at", "")
                if updated:
                    from datetime import datetime
                    dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                    if dt.timestamp() < cutoff:
                        f.unlink()
                        removed += 1
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"session_cleanup: {e}")
    if removed:
        logger.info(f"session_cleanup: removed {removed} stale sessions")
    return removed


def stats_report_job(scheduler: Scheduler) -> dict[str, Any]:
    """Built-in: log scheduler stats."""
    jobs = scheduler.list_jobs()
    total_runs = sum(j["run_count"] for j in jobs)
    logger.info(f"stats_report: {len(jobs)} jobs, {total_runs} total runs, {scheduler.uptime_s:.0f}s uptime")
    return {"jobs": len(jobs), "total_runs": total_runs, "uptime_s": scheduler.uptime_s}


# ─── Install built-in jobs ────────────────────────────────────────────────────

def install_builtin_jobs(scheduler: Scheduler, config_dir: str = "~/.nanobot_lite") -> None:
    """Install the three built-in jobs."""
    scheduler.every(300, "health_check", health_check_job)
    scheduler.every(1800, "session_cleanup", session_cleanup_job, config_dir)
    scheduler.every(3600, "stats_report", stats_report_job, scheduler)