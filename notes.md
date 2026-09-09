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

Authoring restrictions added in phase 4 (`scenario.py`, `marketplace_policy.py`,
`marketplace_engine.py`, `policy_runtime.py`) -- eight new `MarketplaceParameters`
keys, all default-off, so every preset's resolved/manifest gains only the new
schema, never a changed value:
- `surcharge_minor`/`surcharge_driver_share` (flat post-multiplier surcharge,
  with a commission-exempt driver share), `commission_binding` (`"offer"` or
  `"quote"`), `driver_lockout_seconds`, `guarantee_window_seconds`,
  `announce_terms`, `service_area` (`None`, a declared zone id, or a box).
- `service_area` is validated in its *authored* form (`Params.check` builds
  `MarketplaceParameters(**scalars)` before any compilation step can resolve
  a zone id), so a string is accepted as a nonempty zone id and only
  `_compile_policy` resolves it against `world.zones`; an unknown id is a
  compile error naming the declared ids.
- `ConditionalRule` gains an optional `start`/`end` window (both or
  neither, `end > start`); `PlatformPolicy` gains `programs` (unique ids;
  every program's `window_seconds` must equal the platform's own
  `guarantee_window_seconds`, and a program requires it to be positive); a
  rule may not itself change `guarantee_window_seconds` from the base
  (it is one platform-wide cadence, not a per-segment one).
- `Campaign` gains `budget_minor` (nonnegative or `None`) and
  `max_completed_rides` (positive or `None`).
- A `regulation` intervention requires at least one cap, with
  `max_base_fare_minor`/`max_per_km_minor` jointly set or jointly absent;
  `impose_regulation` (the engine command it schedules) enforces the same
  rule at apply time. `issue_quote`/`create_offer` raise `RegulationRejected`
  (a `CommandRejected` subclass) when the in-force regulation's cap is
  exceeded -- nothing is disclosed beyond `command_result:
  regulation_rejected`.
- A `policy_change` intervention (wildcard or not) may not change
  `guarantee_window_seconds` -- rejected at compile time, since that cadence
  is scheduled once, from the platform's launch-time policy, and never from
  a later intervention or a restore.
- `Quote` gains `commission_fraction`/`driver_surcharge_minor` (both
  default-valued, restored with `.get` tolerance); `SNAPSHOT_SCHEMA_VERSION`
  stays 2 (additive), the same precedent phase 3's `transfers` table set.

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
