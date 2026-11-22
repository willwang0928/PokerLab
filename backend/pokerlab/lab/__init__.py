"""The AI Simulation Lab (v0.5.0): strategy-versus-strategy simulations with duplicate
deals, honest intervals and reproducible seeds.

* :mod:`.strategies` — the agents that can be compared (existing agents, unchanged)
* :mod:`.config` — what an experiment is and how it splits into reproducible sessions
* :mod:`.simulate` — playing one session (in a worker process)
* :mod:`.stats` — intervals, p-values, Holm correction
* :mod:`.report` — matchup reports and round robins
* :mod:`.jobs` — background execution, progress, cancellation and caching

Command line: ``python -m pokerlab.lab advanced intermediate --hands 10000 --seed 1``.
"""

from .config import LabConfig
from .jobs import Busy, LabJobs, run
from .strategies import STRATEGIES

__all__ = ["STRATEGIES", "Busy", "LabConfig", "LabJobs", "run"]
