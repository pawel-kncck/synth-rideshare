# Scenario 4 analysis — Cold-Start Market Entry via Subsidized Rider Vouchers

- **Model id:** `grok`
- **Review date:** 2026-09-09
- **Repository revision:** `28e839eae51358d7646ae0a5f156508ba30b1c11` (default branch `main` tip at review time)
- **Method:** Independent code/docs inspection via GitHub MCP and raw file reads. No scenario run; no other models’ reports consulted. Conclusions below are from inspection unless noted.

**Assumptions used in this review**

- Map Platform Dominant → `rebu` and Platform Challenger → `flyt` (third preset platform `blot` can remain memberless / unused). The simulator only ships named platforms `rebu` / `blot` / `flyt` (`scenario.py` `ALL_APPS`, ~L642).
- “$3.00 base + $1.50 per unit” means `base_fare_minor=300` and `per_km_minor=150` with `minor_units_per_major=100` (currency is integer minor units; distance is Cartesian km). The scenario’s “unit” is not otherwise defined.
- “Vicinity” for peer adoption is a geographic neighborhood; no radius is specified in `DESCRIPTION.md`.

---

## 1. What is currently possible

### 1.1 Multi-platform cold start (unlaunched Challenger, launch at t=24)

**Supported.** Platforms have a `launched` flag; unlaunched platforms reject participation. Evidence:

- `marketplace_engine.py`: `Platform.launched` (~L180); `launch_platform` (~L680–681); quote/order paths reject unlaunched platforms (~L793–794, ~L894–895, ~L925–926).
- Scenario authoring: `launch(...)` interventions (`scenario.py` ~L596–597); `platforms.<id>.launched: False` as in `scenarios/flyt_launch_week.py`.
- Runtime applies launches before policy/preference interventions at the same time (`scenario.py` `INTERVENTION_ORDER`, ~L37; `policy_runtime.py` `_intervene`, ~L626–627).

**Config sketch (mechanisms already present):** Challenger starts `launched=False`, population segments install only Dominant, `awareness` includes Challenger, intervention `launch(..., at_hours=24, platform="flyt")`. Compiler forbids initial membership of an unlaunched app (`scenario.py` `_check_access`, ~L1162–1164) and warns if a platform never launches (~L1052–1053).

**Observable check 1 (t=0..24: zero Challenger dispatches/installs/completions):** Enforced by the engine for quotes/orders/offers while unlaunched. Installs only occur in `EvolutionPolicy` checkpoint handling after the app is in `known_launched_apps` (`policy_runtime.py` `_checkpoint`, ~L651–670), so pre-launch downloads should also be zero if checkpoints fire before launch—**provided** adoption rates are zero before launch or checkpoints see Challenger as not launched. After launch, installs become possible.

### 1.2 Population size, horizon, Dominant-only initial access

**Supported via scenario overrides.** `world.horizon_hours`, `population.riders.count`, `population.drivers.count`, and segment `apps` / `preferred_app` / `awareness` are first-class (`scenario.py` schema and builders; examples in `scenarios/flyt_growth_month.py`). A 168h horizon with 250 riders / 50 drivers and 100% Dominant install is configuration, not new code.

### 1.3 Dominant tariff and 20% commission; Challenger timed commission schedule

**Supported.**

- Tariffs: `MarketplaceParameters.base_fare_minor`, `per_km_minor`, `commission_fraction` (`marketplace_policy.py` ~L62–68, ~L99).
- Driver payout uses gross × `(1 - commission_fraction)` and is **not** reduced by rider discount (`marketplace_policy.py` `dispatch` ~L275; normative formula in `plans/architecture/marketplace-policy.md`).
- Timed commission change: `policy_change` intervention swaps a compiled policy version at a simulated time (`scenario.py` builders; applied in `policy_runtime.py` ~L630–633). Pattern already used in `scenarios/flyt_growth_month.py` (`commission_fraction: .15` after day 7) and `scenarios/blot_discount_week.py` (tariff change at hour 96).

So Challenger can start at `commission_fraction=0` from launch and switch to `0.15` at `at_hours=96`. That matches “0% for first 72h of operation after t=24, then 15%” as a **platform-wide** schedule. Existing quotes/offers keep committed terms across the boundary (documented and consistent with freeze-on-commit design).

### 1.4 Time-bounded rider discounts funded by the platform (campaigns)

**Partially supported as windowed campaigns, not as a 3-ride voucher.**

`Campaign` supports half-open `[start, end)`, fixed or fractional rider discount, optional `discount_cap_minor`, driver `bonus_minor`, `segment`, `new_user_only`, and `awareness` (`marketplace_policy.py` ~L19–58; scenario builder `campaign(...)`, `scenario.py` ~L551–556).

Pricing path:

- Quote applies the best eligible campaign discount (`MarketplacePolicy.quote`, ~L250–255).
- Settlement residual `platform_contribution_minor = rider_payment - driver_payout` (`marketplace_engine.py` `_settle`, ~L1243–1248) **allows negative contribution** (architecture doc explicitly allows it).

A Challenger campaign such as `discount_fraction=1.0`, `discount_cap_minor=1000` ($10), `start_hours=24`, `end_hours=96`, `awareness="announced"` therefore produces platform-funded rider discounts in that window, and can drive negative Challenger contribution when rider payment is near zero while drivers still receive payout (especially at 0% commission).

**Limit:** eligibility is only `segment` + `new_user_only` (i.e. `completed_rides == 0` via `policy_runtime.visible`, ~L136–140). There is **no** “first N completed rides” campaign predicate. Architecture states: “Aggregate budgets, first-N redemptions, and multi-ride quests are extensions” (`plans/architecture/marketplace-policy.md`).

So “First 3 Rides Free up to $10” is **not** currently expressible; “first ride free up to $10 for new Challenger users during the window” is.

### 1.5 Checkpoint-based app adoption / awareness (coarse substitute for mid-run growth)

**Supported as a different mechanism than the scenario’s triggers.**

- `EvolutionTraits.adoption_rate_per_day`, `adoption_friction`, `onboard_car` (`behavior_policy.py` ~L94–111).
- `EvolutionPolicy.checkpoint` draws at most one download from known launched apps not yet installed, using a combined hazard `p = 1 - exp(-rate * elapsed_days)` (`behavior_policy.py` ~L346–360).
- Checkpoints are scheduled from `evolution.checkpoint_hours` / `first_checkpoint_hours` (`scenario.py` ~L1102–1110; `main.py` schedules them).
- Install path records `app_installed`, `account_activated`, optional `car_registered` (`policy_runtime.py` ~L667–677).
- Example: `scenarios/flyt_launch_week.py` (unlaunched Flyt + awareness + daily adoption + launch campaign).

This can grow Challenger membership after t=24, and installs are decoupled from immediate usage (download does not auto-prefer or auto-open the app; `behavior-policy.md` / `PersonProfile` separation of apps vs preferred vs open).

**It does not implement:** rider install when Dominant ETA > 8 minutes; peer adoption > 20% in vicinity; driver 10% per idle hour upon learning 0% commission. Those are absent from `EvolutionPolicy` inputs (`declaration` observations ~L306–308: no ETA, no neighbor fraction, no idle flag, no commission observation).

### 1.6 ETA-sensitive **choice among already installed apps** (not install)

**Supported for usage decoupling after install.**

`RiderPolicy.decide` inspects other **usable** (installed) apps when price/ETA excess or missing supply triggers inspection (`behavior_policy.py` ~L216–221), then purchases the best observed quote vs outside option (~L223–237). Setting `eta_tolerance_seconds` (e.g. 480 for 8 minutes) changes inspection/purchase sensitivity to Dominant ETA, but only after Challenger is already installed. That supports observable check 4’s “installed but may choose Dominant due to ETA,” **not** the scenario’s install rule.

### 1.7 Observability of money, completions, and installs (with caveats)

**Mostly supported from raw logs; built-in metrics are coarser.**

- Whole-run `platform_contribution`, `rider_payments`, `driver_payouts`, and per-platform **completion counts** (`metrics.py` `calculate_metrics`, ~L345–382).
- Settlements in final state / log include `platform_id`, amounts, and reason (`Settlement` in `marketplace_engine.py` ~L368–378).
- Policy observations include `app_installed` with timestamp, role, person, platform (`policy_runtime.py` ~L669–670); final snapshots retain `platform_memory[..].completed` per person (`visible` / increment ~L136–140, ~L471–476).
- Interval series (`--interval-minutes`) track sessions/offers/completions/utilization but **not** money or installs (`metrics.py` `aggregate_intervals`, ~L50–76).
- Market-share helper is completion shares by period, not contribution (`aggregate_market_share`, ~L294–320).

**Check mapping:**

| Check | Feasible today? |
| --- | --- |
| 1. Zero Challenger activity before t=24 | Yes (engine + install gating); confirm from orders/offers/settlements/`app_installed` timestamps. |
| 2. Challenger negative contribution t=24..96 | Raw settlements filterable by `platform_id` and time; built-in summary is **global** contribution, not per-platform or windowed. Negative values are mechanically possible when discounts apply. |
| 3. Exactly first three Challenger rides vouchered | Per-rider Challenger `completed_rides` exists in platform memory, but **no** mechanism applies $10 off on rides 1–3 then standard pricing. Only `new_user_only` (ride 0→1). |
| 4. Install counts ≠ usage | Yes: compare `app_installed` / membership in snapshots vs orders by `platform_id`; rider multi-app choice already exists. |

---

## 2. What is almost possible with a small, quick, independent adjustment

### 2.1 Per-rider “first N completed rides” discount eligibility on campaigns

**Gap:** Scenario needs $10 off on each rider’s first **three** completed Challenger rides, then standard pricing. Today `Campaign.eligible` only gates on time, optional segment, and `new_user_only` (`marketplace_policy.py` ~L51–54). `completed_rides` is already computed per platform/role/person and exposed in `visible` (`policy_runtime.py` ~L136–140) and allowed in **conditional tariff rules** (`VISIBLE_FIELDS`, `marketplace_policy.py` ~L108) but **rules cannot attach discounts**—discounts come only from campaigns (`quote` ~L250–255).

**Smallest adjustment:** Extend `Campaign` with something like `max_completed_rides: int | None` (or `completed_rides_lt`) and honor it in `Campaign.eligible` using `visible['completed_rides']`. Mirror the field in `scenario.py` `CAMPAIGN` schema (~L470–475) and `campaign(...)` builder (~L551–556). Then configure Challenger: `discount_fraction=1`, `discount_cap_minor=1000`, window `[24h, 96h)`, `max_completed_rides=3`.

**Why small/independent:** One dataclass + eligibility predicate + schema binding; no new settlement reservation, budget ledger, or quest state machine. Uses existing per-platform completion counters (incremented on `order_completed`, `policy_runtime.py` ~L471–476). Canceled rides already do not advance the counter (architecture; increment only on completed).

**Verification:** Seeded fixture with one rider completing four Challenger rides during the window; assert discounts on settlements/quotes for the first three only; fourth uses zero campaign discount; driver payouts remain based on gross.

**Still approximate vs scenario:** Window end at t=96 still cuts off unused voucher rides (scenario implies voucher ends at t=96 anyway). Does not add aggregate burn-rate budgets.

### 2.2 Convenience metrics for Challenger windowed contribution and install-vs-usage

**Gap:** Check 2 wants Challenger **net platform revenue** between t=24 and t=96. Data exist in settlements; `metrics.py` only emits whole-run global contribution (~L379) and completion counts by platform (~L354–356), not contribution by platform × time window, and intervals omit money.

**Smallest adjustment:** Offline aggregation in `metrics.py` (or a tiny analyzer script) grouping `completed_ride` settlements by `platform_id` and optional `[start, end)` seconds; optionally count `app_installed` observations vs completed orders per platform. Does not change the simulator.

**Verification:** Recompute from a known log and compare to manual sums of settlement rows.

### 2.3 Coarser adoption cadence calibration (not the scenario’s triggers)

**Gap:** Driver “10% adoption probability per hour while idle” and rider ETA/peer install rules are missing. Hourly checkpoints (`evolution.checkpoint_hours=1`) plus calibrated `adoption_rate_per_day` can increase download frequency after launch (`EvolutionPolicy.checkpoint`, `behavior_policy.py` ~L346–360; docs note coarse-time approximation and finer checkpoints).

**Why only “almost” / limited:** This remains a **population hazard at checkpoints**, not idle-conditional, not commission-announcement-triggered, not ETA-triggered, and not peer-spatial. It is a calibration knob on an existing mechanism, useful for *some* cold-start volume, but it does **not** satisfy the scenario’s stated adoption rules. Prefer treating true trigger parity as section 3.

---

## 3. What is not currently possible and what must be developed

### 3.1 Event-driven rider install: Dominant ETA > 8 minutes

**Missing behavior:** Install Challenger mid-run when a Dominant quote’s ETA exceeds 8 minutes.

**What exists instead:** ETA excess only triggers inspection of **already installed** apps (`RiderPolicy.decide`, `behavior_policy.py` ~L216–221). Downloads occur only inside `EvolutionPolicy.checkpoint` (`behavior_policy.py` ~L314–360; `policy_runtime.py` ~L641–680).

**Needed:** Either (a) a rider-policy (or evolution) hook that can propose `Evolve(download=...)` / install from quote-time context when ETA threshold fires, with legality checks for launched platforms and awareness, or (b) a new observation channel feeding evolution more frequently than checkpoints. Must preserve “download ≠ prefer ≠ open” and engine install/activate separation.

**Dependencies:** Awareness + launched gating; careful interaction with search budgets (`max_app_visits`, order attempts); reproducibility of randomness identities.

**Success check:** Rider with only Dominant installed receives Dominant ETA > 480s, installs Challenger without waiting for a checkpoint, then may quote Challenger on the same or a later intent.

### 3.2 Peer adoption in geographic vicinity > 20%

**Missing behavior:** No peer/neighbor/vicinity/social-fraction inputs appear in evolution or rider contexts (`EvolutionPolicy.declaration` ~L306–308; `rider_context` in `policy_runtime.py` ~L190–196). No code paths matched geographic peer adoption in engine/policy modules inspected.

**Needed:** Define vicinity (radius or grid cell on `map_km`), compute fraction of nearby riders (or people) who have Challenger installed, expose that as a detached observation, and make it an adoption hazard or deterministic threshold. Requires efficient spatial queries over the population each decision/checkpoint and clear cohort definitions (riders only? online only?).

**Approximation loss if skipped:** Checkpoint hazard adoption can raise aggregate install rates but loses local cascade dynamics—the modeling challenge called out in `DESCRIPTION.md`.

### 3.3 Driver idle-hourly adoption tied to learning 0% commission

**Missing behavior:** Drivers do not adopt because they observe Challenger’s 0% commission while idle. Driver expansion opens **already usable** apps after `no_offer_seconds` (`DriverPolicy.expand`, `behavior_policy.py` ~L255–278). Announced campaign salience affects ranking among known apps for search/adoption hazard (`policy_runtime.announced`, ~L176–188) but:

- Commission is not a campaign field; it is a tariff parameter.
- Adoption hazard does not condition on idle/offer-seeking phase.
- There is no “10% per idle hour” clock separate from checkpoint `elapsed_days`.

**Needed:** An idle-phase adoption process (event while `free_slots`/no commitments, or high-frequency checkpoints with idle gating) that reacts to announced Challenger driver economics (0% commission and/or bonuses), with ~0.1/hour success probability. Likely touches `DriverPolicy` and/or `EvolutionPolicy` plus runtime scheduling.

**Success check:** With Challenger launched at 0% commission, idle Dominant-only drivers accumulate installs at roughly 10%/idle-hour without requiring a daily checkpoint lottery that also fires while busy.

### 3.4 True multi-ride voucher / quest accounting beyond eligibility

Even after §2.1’s `max_completed_rides` eligibility, richer promo semantics (reserved redemptions, burn-rate caps, stacking rules, “voucher inventory” separate from completed_rides) are explicitly out of scope today (`marketplace-policy.md` extensions paragraph). If the research question needs budgeted burn-rate as a hard constraint—not just emergent negative contribution—that requires commitment reservation/release before advertising discounts (architecture warning: do not implement budgets by silently cutting promised payments at settlement).

### 3.5 Built-in guarantee of the business outcome

The scenario’s research question (“does engagement persist when promos end?”) is an **outcome to measure**, not something configuration can guarantee. Post-t=96 behavior depends on learning (`learning_rate`, scores), preference switching, remaining supply, and residual installs. Learning/preference machinery exists (`EvolutionPolicy.checkpoint`) but is orthogonal to voucher correctness. No gap to “implement persistence”; the gap is implementing the **treatment mechanisms** so the experiment is well-defined.

---

## 4. Documentation quality and code readability

### What helped

- Root `README.md` clearly maps files to responsibilities, shows scenario authoring (`campaign`, `launch`, `policy_change`), log schema, and offline `metrics.py` usage.
- `plans/architecture/marketplace-policy.md` and `behavior-policy.md` are unusually precise about formulas, freeze-on-commit, campaign limits, and **explicit non-features** (first-N redemptions, quests, budgets). That made section 3 calls higher confidence.
- Worked examples `scenarios/flyt_launch_week.py`, `blot_discount_week.py`, and `flyt_growth_month.py` demonstrate cold-start launch, campaigns, and commission interventions better than prose alone.
- Code is compact and consistent: `Campaign.eligible`, `visible()`, `EvolutionPolicy.checkpoint`, and `_settle` are short enough to verify claims by reading.

### What hurt or risked misreading

- Scenario review `DESCRIPTION.md` uses Dominant/Challenger and “per unit” while the codebase uses `rebu`/`flyt` and `per_km_minor`; reviewers must infer the mapping (flagged as assumptions above).
- `completed_rides` in **rules** vs **campaigns** is easy to confuse: visibility fields suggest ride-count targeting is generally available, but discounts are campaign-only. A one-line cross-reference in marketplace docs or schema comments would help.
- Adoption docs describe checkpoint hazards well but do not contrast them with event-driven / spatial / idle-hourly adoption—so a reader might over-read `flyt_launch_week.py` as satisfying Scenario 4’s install rules.
- `metrics.py` README promises contribution totals without stating they are not per-platform; Scenario 4’s checks need that distinction.
- No unit test suite (stated in README); confidence rests on inspection and scenario scripts. Line-level behavior of edge cases (e.g., campaign end vs in-flight quotes) is documented but was not executed in this review.

### Actionable suggestions

1. Document campaign eligibility fields vs `ConditionalRule`/`VISIBLE_FIELDS` side-by-side; note first-N rides as unimplemented.
2. Add a short “cold start” cookbook: unlaunched platform + awareness + launch intervention + campaign + adoption rates, with explicit “does / does not” bullets for ETA install, peer vicinity, idle-hourly driver adopt, first-N vouchers.
3. Extend metrics docs (or CLI) with per-platform settlement aggregates and optional time windows for promo burn analysis.
4. In scenario-review `DESCRIPTION.md`, define “unit,” vicinity radius, and platform name mapping to reduce ambiguity for implementers.

---

## Summary judgment

Scenario 4’s **market structure** (two-sided multi-homing capable market, delayed Challenger launch, timed tariffs/commissions, windowed platform-funded discounts, install logs, negative contribution accounting, install≠usage choice among installed apps) is largely within the current Phase 3 design. The **distinctive cold-start mechanisms**—per-rider three-ride vouchers, ETA-triggered installs, geographic peer cascades, and idle-hourly commission-driven driver adoption—are **not** implemented; the closest shipped pieces are `new_user_only` campaigns, checkpoint adoption hazards, and ETA-based multi-app **search**. A small campaign eligibility extension would unlock the voucher check; the adoption cascade rules need new behavioral/spatial machinery.
