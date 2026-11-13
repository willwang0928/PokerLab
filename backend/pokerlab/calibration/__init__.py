"""Calibration of the coach and the leak detector (v0.4.5).

Simulated players with *known, injected* tendencies play the real engine against the
real opponents. Their decisions are graded by the real coach, saved as real history
records, and fed to the real leak detector. Separately, an independent oracle
(:mod:`.oracle`) estimates what each decision actually cost, by playing the rest of the
hand out many times with the true cards and the true policies: no range model, no
one-street assumption, and nothing the coach computed.

Run ``python -m pokerlab.calibration --help``. The full run is slow (tens of minutes on
8 cores) and is not part of the ordinary test suite; ``tests/test_calibration.py`` runs
a small, fast version of every step.
"""
