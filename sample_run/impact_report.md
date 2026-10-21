# Impact report

The assignment is randomized. `replay/arms.csv` puts each user in `agent` or `holdout` before any replay outcome exists. Holdout users receive no action. The primary comparison is those two arms, inside the same goal, after the deadline.

A user enters the cohort only when this same policy selects a goal. Stale opens, store timeouts and users with nothing to solve stay out. Inconclusive judgments stay out of the rates.

## Primary estimate

### `G01`

- Agent arm: 20 / 57 (0.351), 0 censored
- Holdout: 15 / 51 (0.294), 0 censored
- Difference: 0.057
- Approximate standard error: 0.090.
- This gap is inside the noise of this sample. It is not a win.

### `G02`

- Agent arm: 6 / 36 (0.167), 0 censored
- Holdout: 4 / 36 (0.111), 0 censored
- Difference: 0.056
- Approximate standard error: 0.081.
- This gap is inside the noise of this sample. It is not a win.

### `G03`

- Agent arm: 1 / 2 (0.500), 0 censored
- Holdout: 1 / 2 (0.500), 0 censored
- Difference: 0.000
- Approximate standard error: 0.500.
- This gap is inside the noise of this sample. It is not a win.

### `G05`

- Agent arm: 2 / 32 (0.062), 0 censored
- Holdout: 2 / 38 (0.053), 0 censored
- Difference: 0.010
- Approximate standard error: 0.056.
- This gap is inside the noise of this sample. It is not a win.

### `G06`

- Agent arm: 1 / 20 (0.050), 0 censored
- Holdout: 2 / 14 (0.143), 1 censored
- Difference: -0.093
- Approximate standard error: 0.105.
- This gap is inside the noise of this sample. It is not a win.

### `G07`

- Agent arm: 14 / 415 (0.034), 7 censored
- Holdout: 17 / 423 (0.040), 6 censored
- Difference: -0.006
- Approximate standard error: 0.013.
- This gap is inside the noise of this sample. It is not a win.

### `G08`

- Agent arm: 2 / 15 (0.133), 0 censored
- Holdout: 4 / 18 (0.222), 0 censored
- Difference: -0.089
- Approximate standard error: 0.132.
- This gap is inside the noise of this sample. It is not a win.

### `G09`

- Agent arm: 2 / 13 (0.154), 0 censored
- Holdout: 1 / 11 (0.091), 1 censored
- Difference: 0.063
- Approximate standard error: 0.132.
- This gap is inside the noise of this sample. It is not a win.

### `G12`

- Agent arm: 8 / 15 (0.533), 7 censored
- Holdout: 5 / 18 (0.278), 11 censored
- Difference: 0.256
- Approximate standard error: 0.167.
- This gap is inside the noise of this sample. It is not a win.

### Pooled across goals

- Agent arm: 56 / 605 (0.093)
- Holdout: 51 / 611 (0.083)

Pooling mixes goals with different base rates. Use it only as a summary. The per-goal table is the estimate.

## Why served-versus-everyone misleads

People who receive a home page were online, eligible, and in a dialect that had enough titles. Everyone else includes the holdout, users the agent refused to touch, users who never opened the app, and users who had already finished the goal. That gap is a selection effect.

Inside this replay the served cut matches the arm split: a cohort user was served only when the arm was agent. That agreement is not an impact estimate. The pilot below is the cut that goes wrong.

- Served and judged: 0.093 (56 / 605).
- Cohort users who were not served: 0.083 (51 / 611).

The pilot write-up makes the same mistake on a larger scale. It is reproduced here from the pilot files, not used as this run's result.

- Pilot headline: Served users reached their goal 40.1% of the time against 17.7% for everyone else.
- Pilot served rate: 0.401 (217 users).
- Pilot everyone-else rate: 0.177 (3674 users).
- Pilot treatment arm: 0.183 (1912 users).
- Pilot holdout arm: 0.196 (1979 users).

The holdout gap is the comparison that matches the assignment. The headline gap does not.

Triggered population in this replay: 1746 users.
