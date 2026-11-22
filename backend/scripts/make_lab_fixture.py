"""Regenerate the Simulation Lab fixtures used by the frontend tests.

    cd backend && python scripts/make_lab_fixture.py ../frontend/src/test/lab.fixture.json

Runs real experiments through the API (sessions in-process) and saves the strategy list,
a finished head-to-head, a finished round robin and a job captured while running.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from pokerlab.api.app import create_app  # noqa: E402
from pokerlab.lab import LabJobs  # noqa: E402
from pokerlab.lab import jobs as J  # noqa: E402


def finish(client: TestClient, body: dict) -> dict:
    job = client.post("/api/lab/experiments", json=body).json()
    while True:
        view = client.get(f"/api/lab/experiments/{job['id']}").json()
        if view["status"] not in ("queued", "running"):
            return view
        time.sleep(0.05)


def main(path: str) -> None:
    out: dict = {}
    with TestClient(create_app(lab=LabJobs(workers=0))) as c:
        out["info"] = c.get("/api/lab/strategies").json()
        out["info"]["workers"] = 6  # as on a typical machine
        out["h2h"] = finish(c, {"strategies": ["aggressive", "basic"], "hands": 4000, "seed": 42})
        out["rr"] = finish(c, {"strategies": ["passive", "folder", "random"], "hands": 2000,
                               "seed": 7})  # fmt: skip
        # A running job: hold every session after the first.
        gate, real = threading.Event(), J.run_block
        J.run_block = lambda t: (gate.wait(10) if t.session >= 1 else None, real(t))[1]
        body = {"strategies": ["folder", "random"], "hands": 1200, "seed": 5, "session_hands": 100}
        job = c.post("/api/lab/experiments", json=body).json()
        url = f"/api/lab/experiments/{job['id']}"
        while c.get(url).json()["progress"]["hands_done"] < 200:
            time.sleep(0.02)
        out["running"] = c.get(url).json()
        gate.set()
        J.run_block = real
        c.delete(url)
    Path(path).write_text(json.dumps(out))
    print(f"wrote {path}")


if __name__ == "__main__":
    main(sys.argv[1])
