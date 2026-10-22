"""Workers for runs (app/runtime.py): claim a run with a lease, step it until it waits or ends, give it back.

The app starts AGENT_FACTORY_WORKERS threads (default 1; 0 to run none, e.g. when a separate worker process does the
work: `python -m app.worker`). Leases make any number of them safe together: a run is held by one worker at a time, a
worker that stops loses its lease and another picks the run up (app/runtime.py::_recover).
"""

import logging
import os
import threading
import time
import uuid

from app import runtime
from app.gateway.service import Gateway

log = logging.getLogger(__name__)
POLL_SECONDS = 1.0
RETRY_SECONDS = 2.0  # after a failed model call, times the failures so far (the lease outlasts it: 60 s)


def work_once(session_factory, worker_id: str, gateway_factory=Gateway) -> bool:
    """Claim one run and step it until it is no longer running here; False when there was nothing to claim."""
    with session_factory() as session:
        run = runtime.claim(session, worker_id)
        if run is None:
            return False
        gateway = gateway_factory(session)
        while (state := runtime.step(session, gateway, run, worker_id)) in ("running", "retry"):
            if state == "retry":
                time.sleep(RETRY_SECONDS * run.failures)
        return True


def _loop(session_factory, stop: threading.Event, worker_id: str, gateway_factory) -> None:
    while not stop.is_set():
        try:
            busy = work_once(session_factory, worker_id, gateway_factory)
        except Exception:  # noqa: BLE001 - one broken run must not end the worker; its lease runs out and it is retried
            log.exception("worker %s: a step failed", worker_id)
            busy = False
        if not busy:
            stop.wait(POLL_SECONDS)


def start(session_factory, count: int | None = None,
          gateway_factory=Gateway) -> tuple[threading.Event, list[threading.Thread]]:
    count = int(os.getenv("AGENT_FACTORY_WORKERS", "1")) if count is None else count
    stop = threading.Event()
    threads = []
    for i in range(max(0, count)):
        worker_id = f"{os.getpid()}-{i}-{uuid.uuid4().hex[:8]}"
        thread = threading.Thread(target=_loop, args=(session_factory, stop, worker_id, gateway_factory), daemon=True,
                                  name=f"run-worker-{i}")
        thread.start()
        threads.append(thread)
    return stop, threads


def main() -> None:  # pragma: no cover - the separate process form
    from app.db import SessionLocal

    logging.basicConfig(level=logging.INFO)
    stop, threads = start(SessionLocal, int(os.getenv("AGENT_FACTORY_WORKERS", "1")) or 1)
    try:
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":  # pragma: no cover
    main()
