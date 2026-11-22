# Simulation Lab validation (generated)

PokerLab 0.5.0, `python -m pokerlab.lab.validate --reps 200`, 4 workers, 1941 s. Methodology: [`../lab.md`](../lab.md).

## 1. Coverage of the 95% interval (mirror matches, true value 0)

| strategy | dealing | hands per run | runs | covered | share (95% CI) | mean SE bb/100 |
|---|---|---|---|---|---|---|
| Maniac | duplicate | 400 | 200 | 188 | 94.0% (89.8%–96.5%) | 118.8 |
| Maniac | standard | 400 | 200 | 189 | 94.5% (90.4%–96.9%) | 315.7 |
| v0.1 bot | duplicate | 400 | 200 | 192 | 96.0% (92.3%–98.0%) | 36.8 |
| v0.1 bot | standard | 400 | 200 | 188 | 94.0% (89.8%–96.5%) | 58.6 |
| Rock | duplicate | 400 | 200 | 193 | 96.5% (93.0%–98.3%) | 9.2 |
| Rock | standard | 400 | 200 | 190 | 95.0% (91.0%–97.3%) | 56.8 |
| Random | duplicate | 400 | 200 | 183 | 91.5% (86.8%–94.6%) | 254.3 |
| Random | standard | 400 | 200 | 192 | 96.0% (92.3%–98.0%) | 266.5 |
| Intermediate (adaptive, clustered) | duplicate | 1,200 | 40 | 40 | 100.0% (91.2%–100.0%) | 15.3 |

## 2. Duplicate efficiency and speed

Variance reduction = variance of the same hands unpaired ÷ duplicate variance (how many times as many ordinary hands the duplicate run is worth). Speed is hands per second on one core.

| A vs B | hands | A's bb/100 (95% CI) | SE dup. | SE unpaired | reduction | table corr. | hands/s/core |
|---|---|---|---|---|---|---|---|
| Maniac vs v0.1 bot | 20,000 | -189.7 (-218.7 to -160.7) | 14.8 | 16.1 | 1.18× | -0.15 | 996 |
| Calling station vs Rock | 20,000 | -69.4 (-79.2 to -59.6) | 5.0 | 7.0 | 1.98× | -0.49 | 836 |
| v0.1 bot vs Random | 20,000 | +181.5 (+156.3 to +206.8) | 12.9 | 12.9 | 1.01× | -0.02 | 3055 |
| v0.1 bot vs v0.1 bot | 20,000 | -2.7 (-12.8 to +7.5) | 5.2 | 7.9 | 2.30× | -0.56 | 1844 |
| Beginner vs v0.1 bot | 4,000 | -83.7 (-124.5 to -42.9) | 20.8 | 24.5 | 1.38× | -0.27 | 36 |
| Intermediate vs v0.1 bot | 4,000 | +55.4 (+34.4 to +76.5) | 10.7 | 12.9 | 1.45× | -0.30 | 25 |
| Intermediate vs Intermediate, fixed | 10,000 | -0.9 (-12.6 to +10.8) | 5.7 | 11.4 | 5.39× | -0.81 | 9 |
| Advanced vs Intermediate | 4,000 | +20.3 (-19.1 to +59.8) | 17.4 | 27.1 | 2.55× | -0.60 | 5 |

## 3. Duplicate and standard dealing agree

| A vs B | duplicate bb/100 | standard bb/100 | difference (SE) | SE ratio std./dup. |
|---|---|---|---|---|
| Maniac vs v0.1 bot | -210.6 ± 14.7 | -234.1 ± 16.2 | +23.5 (21.9) | 1.11 |
| v0.1 bot vs Calling station | +79.0 ± 6.4 | +89.0 ± 9.3 | -10.0 (11.3) | 1.45 |
