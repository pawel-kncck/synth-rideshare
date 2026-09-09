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

Authoring restrictions added in phase 1 (`scenario.py`):
- `notes` values must be nonempty strings (`Table(Scalar('string'))`); a
  scenario without one fails `Scenario.load` on old saved definitions --
  add `"notes": {}` to migrate.
- `rule().when` keys must be in `marketplace_policy.VISIBLE_FIELDS`
  (currently `segment`, `new_user`, `completed_rides`, `role`); an empty
  `when` or an unrecognized field is rejected at authoring time, before
  `compile_scenario`.
- `market-blank@1` has no platforms and no population segments; it must be
  given at least one platform (`platform()`/`with_changes`) before
  `compile_scenario` succeeds.

Authoring restrictions added in phase 3 (`scenario.py`, `marketplace_engine.py`):
- `platforms.<id>.starting_cash_minor` is a required (nullable) platform key
  (seeds that platform's tracked cash account; `null`, every preset's
  default, means untracked). A definition or saved manifest written before
  phase 3 fails `Scenario.load` with
  `platforms.<id>.starting_cash_minor: missing required setting` -- add
  `"starting_cash_minor": null` per platform to migrate, the same class of
  break `notes` caused in phase 1.
- `driver_cancellation_penalty_minor` (default 0) is a nonnegative-integer
  `MarketplaceParameters` field and applies only to a driver-initiated
  cancellation; `cancel_order(..., driver_penalty_minor=...)` rejects a
  nonzero value for any other cancelling party (`CommandRejected`), before
  any mutation.
- `post_transfer`'s `amount_minor` must be a nonzero integer (signed: positive
  credits the person); `reason` must be one of a closed vocabulary
  (`driver_penalty`, `guarantee_topup`, `lease`, `dividend`,
  `operating_cost`, `grant`); `counterparty="platform"` requires and debits
  `platform_id`, `counterparty="external"` forbids it.

Multi-platform realism, current as of phase 1 (see the linked architecture
docs for the normative contract):
- Baselines: roughly 55% session-to-order with supply, 70% offer acceptance
  at reference conditions, per platform.
- Seeded decisions respond to fare and pickup ETA. Riders prefer cheaper
  fares and shorter pickup ETA; drivers prefer higher payout and shorter
  private pickup delay. Both use a fixed outside/reservation option.
- A platform offers to any driver with a free local commitment slot,
  including one still serving that platform's own ride, and estimates
  pickup from its own known remaining service -- never a competitor's.
  Two platforms' offers to the same driver are never compared jointly
  ([behavior-policy.md](plans/architecture/behavior-policy.md)).
- Rejection or expiry tries the next eligible driver, distinct dispositions
  for each ([marketplace-engine.md](plans/architecture/marketplace-engine.md)).
- The `three-platform-week@1` preset adds weekday commute peaks and
  Friday/Saturday nights through 03:00 the next day, with rotating
  eight-hour crews; `market-blank@1` (phase 1) carries none of that and
  starts with no platforms, for scripts that build their own market.
- `metrics.py` reports observed conversion, acceptance, arrivals and
  pending outcomes by default; `--platform-detail`, `--windows` and
  `--first-choice-days` (phase 1) add per-platform and per-window detail.
  These are synthetic assumptions, not calibration data.
