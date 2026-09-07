Values restrictions (raised up front):

scheduling: times must be finite and not in the past; unknown driver/rider ids are rejected
locations: (x, y) pairs of finite kilometres
clock: cannot move backwards; a run cannot end before the current time
playback: time_scale must be a finite positive number or False (no sleep); zero and True are rejected

Decision probabilities must be finite in [0, 1], sensitivities nonnegative,
and reference fare/ETA positive. Demand peak windows validate weekday and hour
bounds and require multipliers >= 1.

Other existing parameters (speed, fares, delays, shift length) are taken as given and
misbehaves in the obvious way if nonsensical, e.g. a zero speed divides by zero.

Realism implemented:
- Baselines: 55% session-to-order with supply, 70% offer acceptance at reference conditions.
- Seeded decisions respond to fare and pickup ETA. Riders prefer cheaper fares,
  drivers prefer higher fares; both prefer shorter pickup ETA.
- Rejection tries the next eligible driver, separately from offer expiration.
- scenarios/scenario_realistic_week.py adds weekday commute peaks and Friday/
  Saturday nights through 03:00 the next day, with rotating eight-hour crews.
- Reports expose observed conversion and acceptance, arrival counts, pending
  outcomes, and the parameters used. These are synthetic assumptions, not calibration data.
