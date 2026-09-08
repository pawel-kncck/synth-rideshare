Values restrictions (raised up front):

scheduling: only scenarios schedule shifts and trips; the compiler rejects unknown
  driver/rider ids, negative hours, overlapping declared shift windows and same-second trips
locations: (x, y) pairs of finite kilometres, sampled on the world's grid or continuous map
clock: cannot move backwards; a run cannot end before the current time
playback: time_scale must be a finite positive number or False (no sleep); zero and True are rejected
engine commands: money is integer minor units; memberships are nonempty; a driver
  needs a shift and a registered car to open an app; the two-order cap is fixed

Decision probabilities must be finite in [0, 1], sensitivities nonnegative,
and reference fare/ETA positive. Demand peak windows validate weekday and hour
bounds and require multipliers >= 1; segment weights must sum to one.

Other existing parameters (fares, delays, shift length) are taken as given and
misbehave in the obvious way if nonsensical; the engine's World rejects a
nonpositive speed.

Realism implemented:
- Baselines: 55% session-to-order with supply, 70% offer acceptance at reference conditions.
- Seeded decisions respond to fare and pickup ETA. Riders prefer cheaper fares,
  drivers prefer higher fares; both prefer shorter pickup ETA.
- Rejection tries the next eligible driver, separately from offer expiration.
- Rebu offers to drivers with a free own order slot, including one still
  serving a Rebu ride, and estimates that pickup from its known remaining service.
- The three-platform-week@1 preset adds weekday commute peaks and Friday/
  Saturday nights through 03:00 the next day, with rotating eight-hour crews.
- Reports expose observed conversion and acceptance, arrival counts, pending
  outcomes, and the parameters used. These are synthetic assumptions, not calibration data.
