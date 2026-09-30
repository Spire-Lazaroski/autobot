"""Background jobs for long work (parameter search, walk-forward): submit, then poll by id."""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="job")
_jobs: Dict[str, "Job"] = {}
_lock = threading.Lock()
KEEP_SECONDS = 3600


@dataclass
class Job:
    id: str
    kind: str
    status: str = "queued"  # queued | running | done | error
    progress: float = 0.0
    message: str = ""
    result: Any = None
    error: Optional[str] = None
    created: float = field(default_factory=time.time)

    def public(self) -> Dict:
        d = {k: getattr(self, k) for k in ("id", "kind", "status", "progress", "message", "error")}
        if self.status == "done":
            d["result"] = self.result
        return d


def submit(kind: str, fn: Callable[[Callable[[float, str], None]], Any]) -> Job:
    """fn receives a progress(fraction, message) callback and returns a JSON-able result."""
    job = Job(id=uuid.uuid4().hex[:12], kind=kind)
    with _lock:
        now = time.time()
        for k in [k for k, j in _jobs.items() if now - j.created > KEEP_SECONDS]:
            del _jobs[k]
        _jobs[job.id] = job

    def progress(frac: float, msg: str = ""):
        job.progress, job.message = round(min(max(frac, 0.0), 1.0), 4), msg

    def run():
        job.status = "running"
        try:
            job.result = fn(progress)
            job.progress, job.status = 1.0, "done"
        except Exception as e:  # noqa: BLE001
            job.error = f"{type(e).__name__}: {e}"
            job.message = traceback.format_exc(limit=3)
            job.status = "error"

    _pool.submit(run)
    return job


def get(job_id: str) -> Optional[Job]:
    return _jobs.get(job_id)
