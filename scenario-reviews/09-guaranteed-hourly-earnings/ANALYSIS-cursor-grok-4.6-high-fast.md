# Scenario 9 review: Cross-App Exploitation of Guaranteed Hourly Driver Earnings

- **Model identifier:** `cursor-grok-4.6-high-fast`
- **Review date:** 2026-09-08
- **Repository revision:** `28e839eae51358d7646ae0a5f156508ba30b1c11` (clean working tree at review start; this file is the only intended change)

This report treats `DESCRIPTION.md` as requirements to evaluate, not as shipped behavior. Conclusions are primarily inspection-based, with one compile-time probe recorded in section 4. No full scenario run was executed. Other `ANALYSIS-*.md` reports were not present in this folder at review time; this assessment was formed independently.

**Assumptions used only for mapping names onto the shipped three-app schema (not a rewrite of the scenario):**

- Platform Steady and Platform Flash can be represented as two launched marketplace IDs (probe used `rebu` / `blot` after removing `flyt`). The simulator does not ship those product names.
- Currency subdivision in published presets is `minor_units_per_major = 100` (`scenario.py` `_base_v1`), so a $30.00 floor would be 3000 minor units if a payment path existed.
- “Honest / strategic” maps onto exclusive versus multi-app access plus trait differences, because behavior implementations are selected once for all drivers.

**Ambiguities left unresolved (not silently rewritten):**

- “Clock hour online” versus “60-minute evaluation windows”: calendar hours aligned to the world clock, rolling 60-minute windows, or hours of Steady-app-open time are all compatible with the text and are not the same instrument.
- “Gross trip earnings” is not defined relative to the engine’s `FareTerms.gross_minor` (rider-facing) versus `PayoutTerms` (driver-facing, after commission, plus optional completion bonus).
- Zero dispatches in a window: the narrative treats this as 100% acceptance “with zero effort”; the metrics layer treats empty denominators as `null`.
- Timeout versus rejection: the check wants a timeout while serving Flash to increment a rejected-dispatch counter; the engine records `expired` separately from `rejected`.
- “Unavailable” versus “keep Steady online” versus “position where Steady cannot find them” are three different mechanisms; only the third is compatible with the current information-hiding contract.
- “Ghost double-booking” conflicts with the documented, implemented rule that a driver may hold two accepted orders across platforms and physically serve one at a time.

---

### 1. What is currently possible

The existing implementation can stand up the **market skeleton** of Scenario 9 and can record most of the **raw events** the observable checks would later need. It cannot run the guarantee, the fringe surge, or the strategic exploit as specified mechanisms.

**Setup that is configurable today**

- Duration, population, and two platforms compile. `world.horizon_hours`, `population.riders.count`, and `population.drivers.count` are first-class scenario fields (`scenario.py` `SCENARIO`, lines 510–530). A compile probe (see section 4) produced `horizon_seconds = 57600`, 150 riders, and 30 drivers.
- Participant groups can be expressed as segments: 15 exclusive Steady drivers (`apps` a singleton) and 15 multihomers (`apps` a two-platform subset). Largest-remainder assignment from equal weights realized `{'honest': 15, 'strategic': 15}` at seed 0. Access, preferred app, and car registrations are validated in `_check_access` (`scenario.py` ~1148–1168).
- A third preset platform can be removed (`Scenario.remove("platforms", "flyt")`); the compiler requires only “at least one platform” (`compile_scenario`, ~951–952). Product names Steady/Flash are labels; IDs stay whatever the definition declares (`rebu`/`blot` in the probe).
- Shifts can cover the 16-hour horizon (`activity.shifts` rotation or explicit). Drivers start at an exogenous location (`main.py` `_on_shift_start` → `MarketplaceEngine.start_shift`).
- Rider demand can be generated (`weekly`) or listed (`explicit`). Both platforms can be launched from t = 0.

**Shared physical state and multi-platform interaction (the modeling challenge’s substrate)**

- One engine owns people, cars, positions, commitments, and money (`marketplace_engine.py` module docstring; `plans/architecture/marketplace-engine.md`).
- A driver physically serves at most one ride (`Service` / `_start_service`); `MAX_COMMITMENTS = 2` is a global accepted-order ceiling (`marketplace_engine.py` line 33).
- Drivers see their own cross-app work (`DriverView.commitments`, lines 539–547). Platforms do not: `PlatformView` documents that it “never reveals which platform a driver is physically serving” (lines 418–425). `DriverPresence.accepting` is true whenever the app is open and the shift has not requested exit (lines 445–455); `_driver_accepting` (lines 833–840) does not consult competitor commitments.
- Competing platforms may therefore offer to a driver who is already serving the other app. `MarketplacePolicy.candidates` filters only `accepting`, own pending offers, and `own_order_ids` against `max_local_commitments` (`marketplace_policy.py` lines 227–241). This is the shipped information contract, not a bug. The fixture `scenarios/fixture_cross_platform_queue.py` exists specifically to show a Blot accept during a Rebu ride.

This means the **physical** half of the modeling challenge (one body, two apps, competitor work hidden from Steady) is real. The **incentive** half is not.

**Dispatch, timeouts, and acceptance records**

- Steady/Flash-style sequential dispatch is the default: one unresolved own offer, `offer_seconds` (default 10), `order_patience_seconds` (default 60), `max_attempts` (default 5) (`MarketplaceParameters`, `marketplace_policy.py` lines 75–79; `dispatch`, lines 258–279).
- Offer expiry is an engine event (`create_offer` schedules `offer.expire`; `_on_offer_expiry` sets `expired`). Driver reject uses `respond_to_offer(..., accept=False)` → `rejected`. Late accept after the deadline becomes `expired`. Capacity/access races become `acceptance_failed` (`respond_to_offer`, lines 998–1024; `OFFER_DISPOSITIONS`, line 37).
- Platforms are notified of outcomes (`_resolve_offer` → `offer_resolved` with `state`, lines 1046–1056). Runtime re-queues dispatch on non-accept (`policy_runtime.py` `on_notification`, lines 467–468).
- Matching `"nearest"` (default) or `"pickup_eta"` uses observed position. A driver parked far from pickup is a worse candidate, so **explicit** fringe parking plus **explicit** demand elsewhere can reduce Steady offer volume. This is a spatial matching effect, not a guarantee-preserving strategy.

**Money that exists today (not the requested subsidy ledger)**

- Quotes freeze rider `FareTerms`; offers freeze `PayoutTerms` (`payout_minor`, `bonus_minor`). Completion settlement writes one `Settlement` with `reason` in `{completed_ride, cancellation_fee}`, a single `driver_payout_minor`, and `platform_contribution_minor` as the residual (`Settlement`, lines 368–378; `_drop_off` → `_settle`, lines 1167–1168, 1243–1251).
- Campaigns can add a **per-completion** `bonus_minor` and/or a rider discount inside a half-open `[start, end)` window, optionally gated on visible `segment` / `new_user` (`Campaign`, `marketplace_policy.py` lines 19–58; schema `CAMPAIGN` in `scenario.py` lines 470–476). Bonus is chosen at offer time and bound on accept (`MarketplacePolicy.dispatch` lines 273–278; marketplace-policy.md “Freeze driver payout/bonus terms when issuing an offer”).
- Published examples (`scenarios/blot_discount_week.py`) show exactly this: time-bounded rider discount plus `bonus_minor=100`, then a later `policy_change` tariff. That is a completion incentive, not an hourly earnings floor.

**Pricing “surge” that is actually supported**

- `multiplier` is a static tariff input used as `G = max(minimum_fare, multiplier * raw)` (`MarketplacePolicy.quote`, lines 247–249). A compile probe set `platforms.blot.policy.parameters.multiplier = 3.5` successfully.
- Timed `policy_change` interventions can replace compiled parameters (including `multiplier`) at a simulated hour (`scenario.py` `policy_change`; `PolicyRuntime._intervene` lines 628–633). This can approximate **when** Flash is expensive, not **where**.
- `activity.trips.peaks[].multiplier` scales **arrival weights** by clock hour (`_peak_weight` / `_sample_times`, `scenario.py` lines 1389–1418). It is not a price surge and is not spatial.

**Observability of the listed checks, given current logs**

`Simulation.run()` writes JSONL `simulation.log` with initial/final engine snapshots and scoped notifications (`README.md`; `main.py`). `metrics.py` reconstructs market-wide counts from those snapshots; it does not implement Scenario 9’s checks.

| Requested check | Evaluable from current artifacts? |
| --- | --- |
| 60-minute acceptance-rate windows and $30 top-up when organic earnings < $30 | **Windows, only offline and only as a reconstruction.** Final `offers` have `driver_id`, `platform_id`, `created_at`, `state`. An analyst can bucket by 3600s and compute accepted / dispatched. **Top-up does not occur**; there is no guarantee evaluator and no subsidy settlement. `metrics.aggregate_intervals` can emit 60-minute `offer_acceptance_pct`, but that ratio is market-wide, assigned to the offer’s creation interval, and is `accepted_offers / offers` including every platform (`metrics.py` lines 139–144, 251–291). Empty windows are `null`, not 100%. |
| Driver on Flash flagged unavailable to Steady; no ghost double-booking | **Not as specified.** Steady still lists the driver as `accepting` if its app is open. The engine **does** prevent two simultaneous physical services; it **does not** prevent Steady offers or a queued second accept. Notifications `order_assigned` / `service` timelines show actual occupancy after the fact. |
| Timeout while executing Flash increments rejected-dispatch and disqualifies that hour’s top-up | **Timeout is logged** as `expired` (and `offer_resolved` / `offer_closed`). There is **no** rejected-dispatch counter and **no** top-up to disqualify. Expiry and reject are distinct dispositions. |
| Top-up subsidies as a separate ledger line from fare earnings | **Not present.** Settlements collapse `payout_minor + bonus_minor` into `driver_payout_minor` (`PayoutTerms.driver_payout_minor`, lines 167–169). Assignment snapshots retain the split (`payout_minor` vs `bonus_minor`), so a **completion bonus** can be recovered offline; an hourly idle subsidy cannot, because it is never written. `calculate_metrics` totals only settlement fields (`metrics.py` lines 345–379). |

**Default participant behavior versus the exploit**

- `DriverPolicy.respond` is a logistic of payout, private pickup delay, loyalty, and learned score (`behavior_policy.py` lines 281–295). It has no acceptance-rate, guarantee, or “protect Steady AR while sniping Flash” objective.
- App expansion opens additional usable apps after `no_offer_seconds` (`expand`, lines 262–279; default 60s, `multi_app`). Strategic drivers with two apps will tend to keep both open, which is necessary for the narrative but is not strategic idling.
- There is no reposition / deadhead command. After a Flash drop-off the car idles at the destination (`_drop_off` sets `Motion.idle` at the destination). Drivers cannot choose to return to a low-demand fringe.
- One `behavior.driver.implementation` applies to every driver (`compile_scenario` implementations map, `scenario.py` ~953). Honest vs strategic differences must be encoded in `DriverTraits` and app sets, not in two policy classes selected per segment.

**Limits of this “possible” set**

Configurable support here is **population, geography-as-points, two apps, sequential dispatch, per-ride money, and raw logs**. It is not the conditional hourly scheme, not geographic surge, and not the strategic program described in the ASCII diagram.

---

### 2. What is almost possible with a small, quick, independent adjustment

None of the following delivers the full incentive scheme. Each is localized. If a change depends on a new payment path plus AR state plus occupancy leakage, it is in section 3.

**2.1 Offline reconstruction of the first and third observable checks (analysis only)**

- **Gap:** `metrics.py` has 60-minute buckets and offer dispositions but no per-driver, per-platform window and no top-up line.
- **Smallest adjustment:** A throwaway reader over the final snapshot’s `offers`, `orders`, `assignments`, and `settlements` that (a) buckets Steady offers by `[floor(t/3600), floor(t/3600)+1)`, (b) treats `rejected` and optionally `expired` as failures, (c) sums completed Steady `driver_payout_minor` (or `payout_minor` excluding bonus) in that window, (d) prints a **counterfactual** `max(0, 3000 - earnings)` and a would-be disqualification flag. No engine change.
- **Why small:** `collect_metric_records` already walks the same tables; this is another offline fold. `INTERVAL_MINUTES` already includes 60.
- **What it loses:** It does **not** pay drivers, change contribution, or feed back into behavior. Idle 100% AR with zero offers is a definition choice the scenario leaves open.
- **Verify:** After any two-platform run, compare printed windows to raw offer rows.

**2.2 Time-boxed Flash multiplier (already a scenario edit; not geographic)**

- **Gap:** DESCRIPTION wants 2.5×–3.5× **in fringe zones**, periodically.
- **Smallest adjustment:** `policy_change` on Flash `multiplier` (and a later reset). `MarketplacePolicy.controller` cannot do this (`Stop('fixed policy')`, lines 302–306; runtime rejects non-`Stop`, `policy_runtime.py` lines 595–596), but interventions already can.
- **Why small:** Same pattern as `scenarios/blot_discount_week.py`.
- **What it loses:** No fringe, no adjacent-sector trigger, no driver-visible “surge map.” Drivers only see offer `payout_minor`.
- **Verify:** Quotes during the window have `fare` scaled by the new `multiplier`; `selected_rule` / `policy_version` on the quote record the version.

**2.3 Spatial demand / parking via explicit activity (configuration, laborious)**

- **Gap:** `weekly` origins and rotation shift locations are uniform/`grid` samples over `map_km` (`_sample_point`, `scenario.py` lines 1336–1341, 1358–1360, 1374–1376). There are no named zones.
- **Smallest adjustment:** `activity.trips.generator = explicit` with origins clustered away from a chosen fringe, and `activity.shifts.generator = explicit` placing strategic drivers at that fringe. Nearest matching then sends few Steady offers to those coordinates.
- **Why small:** The explicit generators and `trip` / `shift` builders already exist; `fixture_cross_platform_queue.py` uses them.
- **What it loses:** No “adjacent sector” object; no automatic return to the fringe after a Flash trip; no platform-specific demand fields. After the first Flash completion the strategic driver is no longer parked in the idle zone.
- **Verify:** Count Steady offers to those driver IDs versus honest drivers nearer demand.

**2.4 Trait-level approximation of “prefer Flash money / protect capacity”**

- **Gap:** Default `respond` will accept a Steady offer while serving Flash if a slot remains (`second_order_probability` default 1; `MAX_COMMITMENTS` 2).
- **Smallest adjustment:** For the strategic segment, set `second_order_probability` near 0 and/or a large `max_private_pickup_seconds` clamp so a second order is almost never taken; raise `payout_sensitivity` / `acceptance_bias` so large Flash payouts are usually accepted. Honest segment stays exclusive on Steady.
- **Why small:** Segment trait overrides are the documented population API (`behavior-policy.md`; `segment_fields` in `scenario.py`).
- **What it loses:** Not guarantee-aware. A strategic driver will also refuse a **Steady** second order (good for “no double book”) but will not compute “this Flash accept risks a Steady timeout this hour.” They cannot close Steady to hide while remaining “online for the guarantee,” because online-for-dispatch **is** the open app.
- **Verify:** Inspect `policy_decision` / `Respond.probability` and offer `state` for strategic IDs.

**2.5 Split fare versus completion-bonus in metrics (does not create hourly subsidies)**

- **Gap:** Check 4 wants subsidies as a **separate ledger entry**. Completion `bonus_minor` is already stored on `Assignment.payout` but dropped at `_settle`.
- **Smallest adjustment:** Either persist `bonus_minor` (and a `base_payout_minor`) on `Settlement`, or have `calculate_metrics` join settlements to `orders[s.order_id].assignment.payout`. Affected: `marketplace_engine.py` `Settlement` / `_settle`, and/or `metrics.py` money totals.
- **Why small:** Additive fields; existing reasons unchanged.
- **What it loses:** Still only per-ride campaign bonuses. Idle top-ups remain absent.
- **Verify:** A `bonus_minor=100` campaign run shows nonzero split; control without campaigns shows zero bonus.

**2.6 Richer controller/dispatch context for own-offer history (still cannot pay)**

- **Gap:** A custom marketplace policy cannot see a driver’s full own-offer history. `platform_context` materializes `own_offers` only for the **current order’s** `offer_ids` (`policy_runtime.py` lines 153–155). `controller` context is `{now, platform_id}` (lines 592–593).
- **Smallest adjustment:** Include `view.own_offers()` (already implemented on `PlatformView`, lines 492–493) and/or online driver IDs in the controller snapshot, and allow the controller action type to remain `Stop` while writing AR scratch state into `platform_memory`.
- **Why small:** Context widening in one adapter function; no new scheduler domain.
- **What it loses:** Memory can count rejected/expired offers per driver-hour, but **no command exists to create a non-ride settlement**. Without section 3’s payment API this is bookkeeping only.
- **Verify:** Controller `Decision.memory` after a fixture with known rejects contains the expected counts.

Do **not** treat “custom policy that implements the whole guarantee” as a small adjustment. `register_policy` exists, but paying idle drivers, evaluating clock-hour AR, and hiding occupancy from dispatch require engine and contract work in section 3. Segment extra traits on a subclassed `DriverTraits` also collide with `TRAITS['driver'] = DriverTraits` at `Plan.prepare` (`scenario.py` lines 35, 1304–1309) and with `segment_fields` still using builtin `Params(TRAITS[role])` (line 454).

---

### 3. What is not currently possible and what must be developed

**3.1 Conditional hourly earnings guarantee (the core mechanism)**

Missing state and behavior:

- No clock-hour (or rolling-window) evaluator of **organic fare earnings** versus a $30 floor.
- No notion of “online for Steady” distinct from global `Shift` plus `open_apps`.
- No acceptance-rate instrument used for pay. Platform memory tracks `completed` ride counts for `new_user` / `completed_rides` rules only (`policy_runtime.py` lines 136–140, 471–476).
- Campaigns cannot express it. A compile probe adding `guarantee_hourly_minor`, `acceptance_rate_min`, and `window_seconds` was rejected as unknown keys (allowed campaign keys are only those in `CAMPAIGN`, `scenario.py` lines 470–476).
- Architecture text lists “campaign budgets, stacking variants and **quests**” as extensions, not implementations (`plans/architecture/marketplace-policy.md` lines 97–98, 252–253). An hourly guarantee conditioned on AR is a quest-like, time-aggregated incentive with reservation/settlement rules the docs say must exist before advertising such a promise.

What must be developed:

1. **Windowed driver performance records** owned by the platform (or reconstructable online): dispatches sent, accepts, rejects, expiries, Steady-app-open seconds, and completed own-ride payouts, keyed by driver and window. This needs either a new hook on `offer_resolved` / `driver_app_*` / shift events into marketplace policy, or a first-class engine ledger the policy may read.
2. **A settlement path not tied to completing an order.** `_settle` always requires an `order` (`marketplace_engine.py` lines 1243–1251). Idle top-up is the scenario’s central cashflow. Options: `reason="guarantee_topup"` (nullable `order_id`), or a `grant_incentive` command. Contribution accounting must stay exact.
3. **Eligibility rule:** AR ≥ 90% on **dispatches in that window**, plus whatever “online” means. Define whether 0/0 qualifies (narrative yes; metrics convention no).
4. **Disqualification** when a timeout/reject occurs: update the window record and skip the top-up. That is new policy, not a campaign parameter.
5. **Cadence:** `controller` is the only periodic platform hook, and it cannot emit money or tariff changes. Either extend controller outputs or add a dedicated settlement event at window close.

Dependencies: engine money records, runtime command application, snapshot/log schema, and `metrics.py` if the ledger must appear in summaries. Success check: a driver with zero Steady completions, Steady app open for the window, and 0 or qualifying AR receives a **second** settlement (or a settlement with a new reason) of 3000 minor units, separate from any `completed_ride` row; a driver who expires a Steady offer in that window receives none.

Approximation that exists: per-ride `bonus_minor`. It loses idle pay, windowing, AR, and the $30 floor. Paying 3000 on the next completion still pays nothing to the idle exploiter.

**3.2 Geographic, periodic Flash surge in fringe zones**

- Automatic surge is explicitly not shipped (`marketplace-policy.md` lines 97–98, 117–118; `scenario-definition.md` line 209).
- Conditional rules may only equality-match `role`, `segment`, `new_user`, `completed_rides` (`VISIBLE_FIELDS`, `marketplace_policy.py` line 108; `ConditionalRule` lines 119–122). Pickup coordinate is not a condition.
- `quote` already sees `request.origin`, so a **hardcoded** custom `quote()` could apply a location multiplier, but marketplace `Implementation.schema_for` **forces** `MarketplaceParameters` (`scenario.py` lines 400–406), so zone geometry cannot be configured. That is a new policy implementation plus, for a real experiment, schema/compiler work—not a one-line preset.
- No zone, sector, or geofence primitive in `World` (`map_km`, `sampling`, `grid_step_km` only).

Success check: quotes for pickups inside a declared fringe during a declared pulse show 2.5–3.5×; pickups outside do not; after the pulse, base tariff resumes. Timed global `multiplier` (section 2.2) loses the spatial predicate.

**3.3 Steady dispatch treating Flash occupancy as unavailable**

The check requires Steady to flag a Flash-engaged driver unavailable and prevent “ghost double-booking.”

Shipped behavior is the opposite information rule: candidate lists use open-app participation and **own** commitments only (`PlatformView.drivers`, `MarketplacePolicy.candidates`; marketplace-policy.md “A platform may offer to a driver who is carrying a competitor's rider”). The engine then **allows** a second accept up to `MAX_COMMITMENTS`.

Making Steady skip those drivers requires one of:

- **Leakage:** a boolean `occupied` / `any_commitment` on `DriverPresence` (small code, large contract change; hidden-state-free candidate lists are an acceptance test in marketplace-policy.md “Validation and acceptance”).
- **Behavior workaround:** strategic drivers close Steady while on Flash—then they are not Steady-online and cannot earn a Steady “online hour.”
- **Global cap 1:** `MAX_COMMITMENTS` is a module constant, not a scenario parameter. Even if set to 1, Steady can still **send** offers; accept would fail as `acceptance_failed` / `no_free_slot`, which is not “flagged unavailable,” and timeout would still be `expired`.

Success check: while a driver has an active Flash `Service`, Steady `candidates()` does not include them; no Steady offer is created; after Flash drop-off they reappear if the app is still open.

**3.4 Strategic exploit behavior (idle on Steady, snipe Flash, protect AR)**

Required program:

1. Stay online on Steady in a low-demand coordinate.
2. Watch Flash for high surge.
3. Accept Flash when the surge is large.
4. If Steady might dispatch during Flash service, either reject Flash or move to where Steady’s matcher will not select them.

Missing pieces:

- No reposition / cruising API. Policies “cannot … move an entity directly” (`marketplace-policy.md` Policy responsibilities; behavior-policy.md “Outputs cannot … move a car”).
- No surge broadcast or “monitor Flash” observation. Drivers learn Flash work only when Flash **offers** them (`offer_received`). They do not see other drivers’ Flash quotes or a zone multiplier.
- `DriverPolicy` has no AR or guarantee terms. A custom `respond` could branch on `commitments` and payout, but `prepare` still instantiates builtin `DriverTraits`, and the driver context has no segment id, no Steady AR, and no future Steady dispatch forecast.
- After a Flash trip, position is the drop-off, so the “stay in the fringe” policy is not stable without deadheading.

This is a new driver-policy model plus (for 4) a movement command or a matcher that ignores distant idle supply—the latter would also change honest drivers.

**3.5 Separate subsidy ledger as an in-simulation fact**

Even after 2.5, `Settlement.reason` has only `completed_ride` and `cancellation_fee`. Check 4 asks that **total Steady payout include top-up subsidies as a separate ledger entry**. That is a new reason (or a child line item) written at window close, visible in the final snapshot and in metrics without joining through assignments. Offline counterfactuals (2.1) are not ledger entries.

**3.6 Distinguishing limitation versus unclear requirement**

| Item | Kind |
| --- | --- |
| No hourly top-up command or AR-conditioned campaign | Implementation limitation |
| Platforms cannot see Flash occupancy | Documented implementation limitation **and** an unresolved tension with the “unavailable” check |
| Clock hour vs rolling window; gross vs payout; 0/0 AR; timeout ≡ reject | Unresolved scenario requirements |
| Ghost double-booking vs two-order cap | Contradictory if read as “must not queue”; already implemented if read as “must not serve two bodies” |
| Steady/Flash product names | Authoring alias only |

---

### 4. Documentation quality and code readability

**What made capabilities discoverable**

- Root `README.md` states the split: scenario compiler, engine legality, policy snapshots, offline metrics. The “at most two unfinished accepted orders” and “platforms cannot query a driver's hidden competitor commitments” sentences are the right prior for this scenario.
- `plans/architecture/marketplace-policy.md` is explicit that automatic surge and quests are **not** advertised. That single paragraph is more reliable than scanning for a `surge` identifier (the word mostly means demand `peaks.multiplier` or tariff `multiplier`).
- `plans/architecture/marketplace-engine.md` table of records lists `Settlement` reasons as `completed_ride` or `cancellation_fee`—enough to rule out a subsidy line without reading `_settle`.
- `plans/architecture/behavior-policy.md` is the full driver model: clocks, expansion, logistic accept, no repositioning. The fixture `scenarios/fixture_cross_platform_queue.py` is the best executable explanation of cross-app queueing.
- `scenario.py` schema objects (`CAMPAIGN`, `MarketplaceParameters` via `POLICY_EXTRA`, `VISIBLE_FIELDS` via rules) are the ground truth for “can I configure X?” Unknown keys fail with the allowed list.

**What was missing, misleading, or scattered**

- There is no document that says “hourly guarantees / acceptance-rate windows are out of scope.” Absence is correct but easy to miss if a reader starts from campaign `bonus_minor` and infers a general incentive DSL.
- “Surge” is overloaded: trip-peak weights, fare `multiplier`, and the unimplemented automatic surge model. A glossary line in marketplace-policy.md or scenario-definition.md would prevent treating `peaks.multiplier: 3.5` as Flash fringe pricing.
- Custom-policy extension is documented (`register_policy`, trusted Python) but the **prepare-time bind to builtin `TRAITS`** and marketplace `schema_for` forcing `MarketplaceParameters` are not called out as limits. A reader could believe a subclassed schema is scenario-authorable end-to-end. It is not.
- Controller documentation (“produces no tariff change”) is accurate; it is easy to hope `interval_hours` is a settlement clock. The runtime’s hard `Stop`-only check is in `policy_runtime.py`, not the architecture table of controller outputs.
- Observability: README describes interval metrics and settlement totals well. It does not say they are **not** per-driver or per-component (base vs bonus vs subsidy). Someone implementing Scenario 9’s checks from `metrics.py` alone would over-read `offer_acceptance_pct`.
- Platform IDs are always Rebu/Blot/Flyt in prose; the schema actually allows any table keys. That flexibility is visible only by reading `Table(PLATFORM)` and `_check_access`.

**Readability of the code paths that mattered**

- `MarketplacePolicy.quote` / `dispatch` / `candidates` are short and match the docs; confidence that AR and geography are unused is high.
- `PlatformView.drivers` comments on the hot path are unusually clear about what `accepting` means and what is omitted. That comment is the decisive evidence for the unavailability check.
- Settlement collapsing bonus into `driver_payout_minor` is one property plus one `_settle` call; easy to miss if one only reads `PayoutTerms` fields.
- `metrics.collect_metric_records` is readable but offers no `platform_id` on the derived offer/search records, which is why interval acceptance cannot be Steady-only without using the snapshot tables directly.

**Actionable documentation / readability suggestions (docs only; not implemented here)**

1. In marketplace-policy.md incentives section, add a negative list: no time-aggregated driver pay, no acceptance-rate gates, no non-completion settlements.
2. Rename or gloss `peaks.multiplier` as `demand_weight` in scenario-definition.md.
3. Document that `DriverPresence.accepting` is app-open ∩ not exiting, not “physically idle.”
4. Note the custom-policy schema trap (`TRAITS` vs `declaration.parameter_schema`).
5. In the metrics README paragraph, state that money totals are settlement-reason aggregates and that bonus is not a separate metric key.

**Verification actually run**

Inspection of the files listed in the review README, plus this compile-time probe (no `Simulation.run`):

```text
Scenario(preset="three-platform-day@1")
  .remove unused segments and platforms.flyt
  .with_changes horizon 16h, 150 riders, 30 drivers,
                blot multiplier 3.5, honest/strategic segments
compile_scenario + Plan.prepare(seed=0)
→ platforms ['rebu','blot'], horizon_seconds 57600,
  drivers {'honest': 15, 'strategic': 15} with expected apps

Adding campaign keys guarantee_hourly_minor / acceptance_rate_min / window_seconds
→ ScenarioError unknown key; allowed list is the CAMPAIGN fields only
```

Conclusions about runtime dispatch-while-busy, settlement reasons, and controller outputs were not re-executed in a live run; they follow the cited functions and the existing cross-platform fixture’s documented purpose.
