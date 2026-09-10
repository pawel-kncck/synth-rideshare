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

Authoring restrictions added in phase 5 (AST-209; `behavior_policy.py`,
`policy_runtime.py`, `marketplace_engine.py`, `marketplace_policy.py`,
`scenario.py`) -- three new registered implementations plus one new
`MarketplaceParameters` key, all default-off:
- `driver_participation@2`, `rider_search@2`, `personal_evolution@2` each
  declare a trait dataclass that subclasses the `@1` one
  (`register_policy`'s `issubclass` check requires this); every new trait
  defaults to `@1`'s value/behavior, so a `@2` selection with default
  traits reproduces `@1` byte-for-byte, draw order included. Shipped
  presets keep selecting `@1`.
- `revise_interval_seconds` (default `0`) is a nonnegative
  `MarketplaceParameters` field; the runtime reads only its base value,
  the same treatment as `guarantee_window_seconds`.
- `Driver` gains `paused_apps` (a `set`, additive, restored with
  `.get`-tolerance to `set()`); `SNAPSHOT_SCHEMA_VERSION` stays 2.
- Population segments and explicit people now resolve each family's trait
  schema from the *selected* implementation's declaration
  (`scenario.trait_schema`), not a fixed `@1` table -- this is what makes
  a `@2` trait authorable through a segment or an explicit person; fixes
  the trap `scenario-definition.md` documented through phase 4.
- The two `@1` mechanical preconditions this needed:
  `RiderTraits.__post_init__`/`EvolutionTraits.__post_init__` now iterate
  their own declared `fields()` instead of `plain(self)`, so a `@2`
  subclass can add non-numeric traits (strings, bools, tuples) without
  those reaching `finite_number`. Behavior-identical for `@1` (same field
  set, same order); landed and gated alone before anything else in this
  phase.

Physical engine extensions added in phase 6 (AST-210; `marketplace_engine.py`,
`behavior_policy.py`, `policy_runtime.py`, `scenario.py`, `main.py`,
`metrics.py`) -- six mechanisms, all default-off:
- `reposition`/`Relocation`: a service-free move, no settlement, no
  platform side; `driver_participation@2` gains an `idle` hook
  (`idle_rule='zone_return'`) that compares expected net per km of staying
  against a zone-return, discounted by deadhead distance, using
  `personal_evolution@2`'s new `zone_scores` memory
  (`zone_learning_rate`). `metrics.driver_distance` gains a `reposition_km`
  bucket, cohorted by relocation id like every other segment.
- `deactivate_driver`: permanent retirement -- ends a live relocation,
  drains not-yet-boarded commitments, refuses any later `start_shift`; a
  scheduled shift for an already-deactivated driver is skipped
  (`main._on_shift_start`) with a `session_skipped` log record instead of
  failing the run.
- `shutdown_platform` and `Platform.insolvency='shutdown'`: cancels open
  orders/offers, closes every app session, leaves a boarded ride to finish
  on frozen terms; the cash rule schedules the shutdown as a zero-delay
  event from `_check_solvency` (called after every settlement/platform
  transfer) so it never re-enters a half-applied transition.
  `metrics.load_run` gains `session_skipped` to its known top-level record
  types (a raw diagnostic record, treated like `notification`).
- `evolution.ledger`: a recurring external `lease` transfer to every
  driver, with an optional `driver_bankruptcy_minor` deactivation check at
  the same posting; `platforms.<id>.dividend`: a recurring equal-split
  payout of tracked cash above a reserve to drivers meeting a per-period
  completed-ride threshold. All of a ledger's/dividend's own configuration
  travels in the scheduled event's payload, never through
  `PolicyRuntime.snapshot()`.
- `shift_end` hook + `Extend`: `driver_participation@2`'s
  `shift_end_rule='extend_to_target'` may propose extending a shift while
  its own net payout is below `daily_net_target_minor`; the runtime
  (`PolicyRuntime.shift_end`), not the policy, is the sole enforcer of
  `max_extension_seconds`.
- `world.speed_zones` (permanent) and a `delay` intervention (temporary,
  zone or box): a per-position speed multiplier consulted once, when a leg
  *begins* (`MarketplaceEngine.travel_seconds`/`speed_kmh_at`), so a leg
  already in progress keeps its planned end. Platform ETA estimates stay
  delay-unaware on purpose, which is what lets `rider_search@2`'s
  pre-existing `eta_drift_cancel_seconds` react to a delay unmodified.
- `Relocation`/`SpeedZone` join `_TABLES` (`SNAPSHOT_SCHEMA_VERSION` stays
  2, same `.get(name, [])` restore tolerance as phase 3's `transfers` and
  phase 4's `regulations`); `Driver.relocation_id`/`deactivated_at`/
  `deactivation_reason` and `Platform.insolvency` restore through the
  existing `.get`-tolerant driver/platform factories.

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
