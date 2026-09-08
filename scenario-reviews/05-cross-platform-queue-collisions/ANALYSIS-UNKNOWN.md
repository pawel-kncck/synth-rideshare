# Scenario 5 analysis: Cross-Platform Chained Dispatching and Queue Collisions

- **Model identifier:** UNKNOWN (explicit; no model id was available to this reviewer)
- **Review date:** 2026-09-09
- **Repository:** https://github.com/pawel-kncck/synth-rideshare
- **Revision:** `28e839eae51358d7646ae0a5f156508ba30b1c11` (main)
- **Method:** Inspection only (raw.githubusercontent.com / curl fetches of listed paths). No clone, no scenario execution, no reading of other `scenario-reviews/*/ANALYSIS-*.md` files. Conclusions below are based on that inspection.

Scenario requirements are taken from `scenario-reviews/05-cross-platform-queue-collisions/DESCRIPTION.md`. Report structure follows `scenario-reviews/05-cross-platform-queue-collisions/README.md`.

Platform names in the scenario (“Blue” / “Green”) are mapped conceptually to the shipped apps `rebu` / `blot` (or any two keys under `platforms`); the engine does not hard-code those marketing names.

---

### 1. What is currently possible

**Setup size, horizon, and two-platform population (requirements 1–2, partially).**  
`scenario.py` presets and `Scenario.with_changes` already express horizon and population counts (`world.horizon_hours`, `population.riders.count`, `population.drivers.count`; see defaults around `scenario.py` ~669–696 and helpers `person` / `segment`). An 8h / 180 riders / 30 drivers market is therefore **configurable** without code changes. Drivers can install and register on both platforms via `apps` / `accounts` / car `registrations` (`behavior_policy.PersonTraits`, `_check_access` in `scenario.py` ~1148–1184). Restricting the market to two launched platforms (dropping Flyt) is also a scenario-definition change over the three-platform presets. Evidence: `plans/architecture/scenario-definition.md` documents the same composition path; `main.Simulation` builds platforms from `plan.platforms` (`main.py` ~51–88).

**Physical capacity: 1 active + at most 1 queued, global across platforms.**  
`marketplace_engine.MAX_COMMITMENTS = 2` (`marketplace_engine.py:33`) caps accepted unfinished orders per driver across all platforms. `DriverView.free_slots` is `MAX_COMMITMENTS - len(commitments)` (`marketplace_engine.py:536–537`). Acceptance enforces the cap atomically (`respond_to_offer` / `_accept`, `marketplace_engine.py:998–1044`); a third acceptance resolves as `acceptance_failed` / `no_free_slot` (`marketplace_engine.py:1020–1021`). Marketplace parameters refuse `max_local_commitments > 2` (`marketplace_policy.py:73–74, 92–94`). Phase-3 and marketplace-engine architecture docs state the same invariant (`plans/phase-3-multi-platform-marketplace.md`, `plans/architecture/marketplace-engine.md`). This directly supports the observable check that no driver exceeds 1 active + 1 queued.

**Chained / forward queue mechanics and drop-off → next pickup origin.**  
A second acceptance is queued without changing motion (`respond_to_offer` docstring and `_accept`, `marketplace_engine.py:998–1044`). When the active service ends, `_release_commitment` promotes `commitments[0]` via `_start_service`, which takes origin from the car’s **actual** position at that time (`marketplace_engine.py:1086–1100, 1185–1191`). That matches the observable check that Blue drop-off location becomes the origin for transit to Green’s queued pickup. Metrics and diagnostics already distinguish queued wait from physical service (`metrics.py` interval note ~34; `policy_runtime` observations `commitment_wait`, `back_to_back_ready`).

**Same-platform forward-dispatch window (parameterized).**  
`MarketplacePolicy.candidates` only offers a second local order when remaining own-service ETA beyond direct travel is within `back_to_back_within_seconds` (`marketplace_policy.py:228–237`). Default is 1800s (`marketplace_policy.py:74`; preset default `scenario.py:638`); the scenario’s **3 minutes** is expressible by setting `back_to_back_within_seconds: 180` on each platform’s policy parameters. Matching is platform-local (`own_order_ids`, `max_local_commitments`).

**Concurrent cross-platform queued offers while serving another platform.**  
`DriverPresence.pending_offer_ids` and `own_order_ids` are **per platform** (`marketplace_engine.py:396–407, 436–456`). Candidate filtering only skips on *this* platform’s pending offers / local commitment count (`marketplace_policy.py:230–233`). Therefore Blue and Green can both hold pending offers to the same driver while the driver has one free global slot. Competing acceptances for the last slot admit one winner; the other fails with `no_free_slot` (phase-3 text; engine accept path above). Drivers can keep multiple apps open (`expansion="multi_app"`, `expand_while_busy`); with `expand_while_busy=True` and high `second_order_probability`, busy drivers remain eligible for second-order offers (`behavior_policy.py:62–63, 264–295`; defaults in `scenario.py:626`).

**Information isolation answering the modeling challenge.**  
`PlatformView` documents that a platform never sees which competitor the driver is serving, competitor destinations, the **hidden global commitment count**, or when the driver becomes free—only shared physical coordinates (`marketplace_engine.py:418–424`). ETA estimators use `own_order_ids` / `own_service` (or `current_position`) only (`marketplace_policy.py:213–225, 284–291`). Driver-side `personal_pickup_eta` / `PolicyRuntime.private_eta` *can* see cross-app commitments for acceptance decisions (`behavior_policy.py:362+`; `policy_runtime.py:413–421`), which is intentional participant knowledge, not platform telemetry. The shipped fixture `scenarios/fixture_cross_platform_queue.py` is an explicit one-driver Rebu+Blot market whose docstring states Blot estimates ETA from current position **without** remaining Rebu service—exactly the Green-vs-Blue occupancy ambiguity. Root `README.md` (~102) and phase-3’s deterministic ETA table document the same design.

**Partial observability of checks from logs/metrics.**  
Raw `simulation.log` records notifications (offers, `commitment_added` / `commitment_released`, `order_assigned`, cancellations, `pickup_eta_revised`) and boundary snapshots including engine driver `commitments` and order `eta_predictions` (`main.py` logging; `marketplace_engine` order fields ~293–300). Offline `metrics.py` exposes `queued_orders_at_end`, offer disposition buckets including `failed_offers` (`acceptance_failed`), and cancellation counts. Capacity violations (or near-misses) and cross-platform queue occupancy are therefore **measurable by an experimenter**, even when platforms themselves cannot see each other’s lock state.

**Limits / assumptions for this section.**  
“Platform Blue / Green” labels are not first-class IDs; use `rebu`/`blot` or custom `platforms` keys. Default `expand_while_busy=False` means drivers may not keep seeking offers on other apps while busy unless the scenario overrides traits. Default `back_to_back_within_seconds=1800` is not the scenario’s 3 minutes until configured. Mid-trip stochastic traffic delay is not a first-class world feature (constant `World.speed_kmh`, `marketplace_engine.py:91–103`).

---

### 2. What is almost possible with a small, quick, independent adjustment

**3-minute forward-dispatch threshold (same-platform).**  
Gap: defaults use 1800s, not 180s.  
Smallest fix: scenario/policy override `platforms.<id>.policy.parameters.back_to_back_within_seconds = 180` (and optionally `matching: pickup_eta`). No code change. Verify with a short explicit fixture mirroring DESCRIPTION timing (active ride ~2 minutes from drop-off, nearby pickup). Cross-platform “within 3 minutes of finishing Blue” cannot be known to Green under current views; that is not a one-line config fix (see §3).

**Drivers remain dual-homed and offer-receptive while busy.**  
Gap: default `expand_while_busy=False` suspends expansion when commitments exist (`behavior_policy.py:264–266`; `policy_runtime.queue_expansion` ~367–376).  
Smallest fix: set driver traits `expand_while_busy: true`, `expansion: multi_app`, keep `second_order_probability` high / `acceptance_bias` saturating (as in the fixture). Independent of engine changes. Verify both apps stay open and second offers arrive during transport.

**Scripted concurrent Blue+Green queue offers during an active Blue trip.**  
Gap: DESCRIPTION’s precise dual-offer narrative is not a stock preset; the existing fixture accepts Blot during Rebu but does not assert dual pending offers or Blue queue-lock detection.  
Smallest fix: extend the explicit-trip style of `scenarios/fixture_cross_platform_queue.py` (timing, locations, fixed tastes) so both platforms open apps, place orders near drop-off, and log concurrent `offer_received` events—still configuration + print assertions, not a new subsystem. Does not by itself give Blue a *platform-visible* lock bit (below).

**Cancel lingering competitor pending offers when the global slot fills.**  
Gap: accepting Green fills `commitments` to 2, but Blue’s outstanding pending offer is **not** auto-canceled; Blue’s matcher still sees `len(own_order_ids) < max_local_commitments` and may keep dispatching until accept fails (`marketplace_engine.py` `_accept` only cancels other offers for the **same order**, lines 1035–1036).  
Smallest fix (engine-local): on successful accept when `len(commitments) == MAX_COMMITMENTS`, resolve other platforms’ pending offers to that driver as `canceled` / `driver_unavailable` (similar to `end_shift` / `close_app` paths at ~764–765, 824–827) and emit `driver_availability` / offer_resolved notifications. Touches `_accept` (and maybe `_release_commitment` to re-open). Independent of rider ETA logic. Verifiable by logging that Blue’s pending offer closes when Green accepts. **Note:** this still does not give Blue a durable “queue locked” presence field without carefully designing what `accepting` means (phase-3 warns against a secret global-idle filter).

**Periodic pickup-ETA revision from observed position (enables drift without Blue telemetry).**  
Gap: `revise` runs on `order_assigned` and when a **same-platform** other commitment completes (`policy_runtime.py:469–480`), not continuously while a cross-platform preceding ride delays the car. `revise_pickup_eta` already appends history and notifies `pickup_eta_revised` (`marketplace_engine.py:985–995`), and Green’s `own_service` ETA from current position *would* drift if revise were invoked as the Blue trip progresses.  
Smallest fix: schedule a bounded periodic `policy.platform.revise` (or revise-on-timer while awaiting pickup) in `policy_runtime` for assigned-not-arrived orders. No need for Blue telemetry—only shared position. Verify `eta_predictions` length/growth during a delayed preceding ride (fixture geometry from phase-3 table).

**Rider cancel when displayed ETA drifts >4 minutes past initial promise (policy-local).**  
Gap: rider `progress` cancels only after fixed `cancellation_after_seconds` from **order creation** (`behavior_policy.py:247–248`), scheduled once on `order_created` (`policy_runtime.py:490`). `pickup_eta_revised` is emitted but **not handled** in `on_notification` for riders (grep shows sole emit site; no consumer).  
Smallest fix: (1) on `pickup_eta_revised`, re-queue rider progress; (2) in `RiderPolicy.progress`, compare latest revised ETA (or absolute promised arrival) to the initial offer/assignment promise and cancel if drift > 240s. Depends on periodic revise above for drift to appear, but the rider rule itself is a localized behavior change. Verify with a fixture that lengthens preceding service / slows world speed so revised ETA exceeds promise by >4 minutes, then assert rider cancel reason.

---

### 3. What is not currently possible and what must be developed

**Mutual platform registration that the driver’s queue slot is “locked/busy” without leaking competitor telemetry.**  
Requirement: once Green’s queue is accepted during a Blue trip, **both** platforms should register queue state as locked/busy.  
Current state: global fullness is enforced only at acceptance (`no_free_slot`). Platforms never observe the global commitment count (`PlatformView` docstring; phase-3: “cannot access … the global commitment queue”). Blue can still treat the driver as locally eligible for a second Blue order while Green already holds the second slot. Experimenter logs can see the truth; **platform policies cannot**.  
What must be developed: an explicit, architecture-compatible signal (e.g. binary “no free capacity” without naming the competing order/platform), or a documented decision that the observable check is experimenter-side only and the wording in DESCRIPTION is over-specified relative to phase-3 isolation. This is not a silent config tweak; it is either a new observation contract or a scenario clarification. Success check: Blue’s `candidates` exclude the driver immediately after Green fills the slot, without Blue reading Green’s order IDs.

**Continuous real-time ETA cancellation evaluation as specified.**  
Requirement: Green rider cancels if pickup ETA increases by more than 4 minutes past the initial promise, continuously evaluated.  
Current state: fixed wall-clock patience from order creation; no promise-vs-revised comparison; no rider reaction to `pickup_eta_revised`; revise itself is not continuous (§2). Approximating with `cancellation_after_seconds ≈ promised_wait + 240` loses the distinction between early long promises and later drift, and does not use revised estimates.  
What must be developed: promise capture at offer/assign time, continuous or event-driven revise, and rider policy that cancels on **drift**, plus tests/metrics comparing `eta_predictions` sequences to cancel events. Dependencies: §2 periodic revise + rider progress hook; optionally richer metrics for “ETA-drift cancellations.”

**Green observing Blue occupancy / remaining work via Blue telemetry.**  
Modeling challenge: can Green observe that the driver is occupied on Blue without Blue telemetry?  
Answer from inspection: **No, by design**—and the simulator already implements that denial. Green only sees position + own orders. Remaining Blue transport time is invisible to Green’s `pickup_eta` (`own_order_ids` only). The fixture exists to show optimistic Green ETAs. Closing that gap with shared telemetry would **contradict** phase-3 / `PlatformView` and is not a desired “fix” unless the research question changes. Success for the challenge is demonstrating the information gap and its ETA error (phase-3 table: Blot initial 3 min vs actual ~10 min), which the engine already supports; “fixing” Green’s blindness is out of scope for Scenario 5 as written.

**First-class mid-trip delay (traffic / long drop-off) that systematically moves Green’s ETA.**  
DESCRIPTION uses an active Blue delay to force Green ETA drift. World motion is constant-speed straight-line (`World.travel_seconds`). There is no traffic event, per-leg speed override, or pause API in the inspected engine. Approximations (lower global `speed_kmh`, longer Blue destination, larger `boarding_seconds`) change the whole market, not a single active trip.  
What must be developed: e.g. temporary speed multipliers, inserted dwell events, or commanded motion holds on a service leg, plus hooks that trigger ETA revise. Until then, drift demos must use geometry/timing rather than true traffic shocks.

**Large stochastic 8h collision experiment as a turnkey scenario.**  
Population scale is configurable, but there is no packaged Scenario 5 definition that (a) forces dual-platform forward dispatch at 3 minutes, (b) instruments concurrent Blue/Green queue races, (c) asserts all four observable checks, and (d) reports ETA-drift cancels. Building that as a robust research run needs the policy/observability pieces above plus careful activity generators—not only `with_changes` counts. The existing fixture is a miniature ETA-blindness demo, not the full intermediate scenario.

**Ambiguity / contradiction to flag.**  
DESCRIPTION’s observable check that **both platforms register** queue lock sits in tension with phase-3’s rule that platforms must not see the global commitment queue and must not use a secret global-idle filter. Assessment assumption: experimenter-level verification of `MAX_COMMITMENTS` and `no_free_slot` is already possible; platform-mutual lock registration is the unresolved product requirement.

---

### 4. Documentation quality and code readability

**What helped.**  
- Root `README.md` maps modules and explicitly calls out `scenarios/fixture_cross_platform_queue.py` and commitment/ETA logging—high signal for this scenario.  
- `plans/phase-3-multi-platform-marketplace.md` and `plans/architecture/marketplace-engine.md` are unusually precise about commitments, queue promotion from drop-off, information boundaries, and the deterministic cross-platform ETA fixture table.  
- `PlatformView` / `DriverView` docstrings and `respond_to_offer` comments state invariants next to the code (`marketplace_engine.py`), which made capacity and isolation conclusions high confidence.  
- `plans/architecture/behavior-policy.md` and `scenario-definition.md` clarify traits (`expand_while_busy`, cancellation patience) vs scenario composition.  
- Typed parameters with validation (`MarketplaceParameters.max_local_commitments > 2` rejected) make hard caps discoverable.

**What hindered or misled.**  
- Scenario folder names “Blue/Green” while code/docs use Rebu/Blot/Flyt; mapping is obvious but undocumented in DESCRIPTION.  
- Default `back_to_back_within_seconds=1800` vs scenario “3 minutes” is easy to miss without reading `MarketplacePolicy.candidates`.  
- `pickup_eta_revised` looks like support for continuous ETA UX, but runtime never feeds it into rider cancellation—docs do not spell out that gap.  
- “Queue locked/busy” language in DESCRIPTION does not appear as an engine state; readers may assume a shared lock object exists.  
- Architecture docs are strong; executable examples beyond the one fixture for *concurrent* dual-platform offers are thin.

**Code readability.**  
Engine and policy modules are dense but coherent (~1.4k lines each for engine/scenario). Commitment lifecycle (`_accept` → `_start_service` → `_release_commitment`) is readable end-to-end. Cross-cutting behavior in `policy_runtime.on_notification` is long and branchy; finding that riders ignore ETA revisions required careful tracing. Naming (`own_order_ids` vs `commitments` vs `free_slots`) correctly encodes the visibility split once understood.

**Actionable suggestions (documentation / readability only; no feature work).**  
1. In Scenario 5 DESCRIPTION (or a link from it), map Blue/Green → example platform IDs and point to `fixture_cross_platform_queue.py` plus `MAX_COMMITMENTS` / `PlatformView` isolation.  
2. Document which observable checks are experimenter-log checks vs platform-visible state.  
3. Note that rider cancel is wall-clock patience today, and that `eta_predictions` / `pickup_eta_revised` are logged but not rider-consumed.  
4. Call out `back_to_back_within_seconds` and `expand_while_busy` as the primary knobs for forward dispatch / busy multihoming.  
5. Add a short “Scenario 5 readiness” subsection to phase-3 or the scenario README listing the remaining gaps in §2–§3.

---

## Requirement checklist (inspection-based)

| # | Requirement | Verdict |
|---|-------------|---------|
| 1 | 8h, 180 riders, 30 drivers, two platforms | Possible via scenario config |
| 2 | All drivers multihome; 1 active + ≤1 queued | Multihome configurable; capacity enforced (`MAX_COMMITMENTS=2`) |
| 3 | Forward dispatch within 3 min of finishing | Same-platform: config `back_to_back_within_seconds=180`; cross-platform finish time not visible to competitor |
| 4 | Concurrent Blue+Green queued offers on Blue trip | Engine allows concurrent cross-platform pending offers |
| 5 | Accept Green → Blue detects queue locked | Global accept fails with `no_free_slot`; Blue does **not** see lock in matching view |
| 6 | Blue delay → Green ETA drift → cancel if >4 min past promise | ETA revise infrastructure exists; continuous revise + drift-based rider cancel **not** implemented; no traffic delay API |
| 7 | Observable checks (capacity, lock, drop-off origin, continuous ETA cancel) | Capacity + drop-off origin yes in engine/logs; mutual lock no; continuous ETA cancel no |
| 8 | Can Green observe Blue occupancy without Blue telemetry? | **No** (intentional); fixture demonstrates optimistic Green ETA |
