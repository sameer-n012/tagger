"""In-memory tracking of background rescan progress, keyed by source id.

The app runs as a single process/worker (see run.sh), so a dict guarded by
a lock is enough here -- there's no cross-process state to reconcile. This
lets long-running scans happen on a background thread while request
handlers (in particular the polled /scan-status endpoint) read progress
without blocking on the scan itself.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

State = str  # "scanning" | "done" | "error"


@dataclass(slots=True)
class ScanStatus:
    state: State
    processed: int = 0
    total: int = 0
    error: str | None = None


_lock = threading.Lock()
_status: dict[str, ScanStatus] = {}


def start(source_id: str) -> None:
    with _lock:
        _status[source_id] = ScanStatus(state="scanning")


def update(source_id: str, processed: int, total: int) -> None:
    with _lock:
        status = _status.get(source_id)
        if status is not None and status.state == "scanning":
            status.processed = processed
            status.total = total


def finish(source_id: str) -> None:
    with _lock:
        _status[source_id] = ScanStatus(state="done")


def fail(source_id: str, error: str) -> None:
    with _lock:
        _status[source_id] = ScanStatus(state="error", error=error)


def get(source_id: str) -> ScanStatus | None:
    with _lock:
        status = _status.get(source_id)
        if status is None:
            return None
        # Return a copy -- callers must not observe a status object that's
        # being mutated concurrently by the scanning thread.
        return ScanStatus(
            state=status.state,
            processed=status.processed,
            total=status.total,
            error=status.error,
        )


def is_scanning(source_id: str) -> bool:
    status = get(source_id)
    return status is not None and status.state == "scanning"
