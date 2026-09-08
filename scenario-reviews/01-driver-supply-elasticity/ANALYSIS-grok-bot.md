# ANALYSIS-grok-bot

- **Model id:** `grok-bot`
- **Review date:** 2026-09-09
- **Repository:** [pawel-kncck/synth-rideshare](https://github.com/pawel-kncck/synth-rideshare)
- **Revision:** `28e839eae51358d7646ae0a5f156508ba30b1c11` (`main` HEAD via GitHub Commits API)
- **Method:** inspection-only (raw file fetches via `curl` / `raw.githubusercontent.com`; no clone, no scenario execution, no code changes)

Independent review of Scenario 1 (*Driver Supply Elasticity Under Asymmetric Platform Take-Rates*) against the shipped simulator. Other `ANALYSIS-*.md` files were not used for conclusions (none were present in the scenario folder listing at review time).

---

## 1. What is currently possible

### 1.1 World, population, and two-platform commercial setup

Most **starting conditions** map onto existing scenario authoring without new engine subsystems.

| Requirement | Support | Evidence |
| --- | --- | --- |
| 24h horizon | Configurable | `world.horizon_hours` in `scenario.py` (`SCENARIO` schema ~L517–520; presets set it in `_base_v1` L666–670) |
| 10×10 grid | Default world | `map_km: [10, 10]`, `sampling: 'grid'`, `grid_step_km: 1` (`scenario.py` L669–670) |
| 200 riders / 40 drivers | Configurable counts | `population.riders.count` / `population.drivers.count` (`_three_platform_day_v1` L686–687 pattern) |
| Identical base fares $2.00 + $1.50/km | Default marketplace params | `base_fare_minor: 200`, `per_km_minor: 150` (`MARKETPLACE_DEFAULTS_V1` L635–636; quote math `marketplace_policy.py` L247–249) |
| No surge / no promos | Defaults | `multiplier: 1`, empty `campaigns` / `rules` (`MARKETPLACE_DEFAULTS_V1` L636–640; `_platform_v1` L645–648) |
| Asymmetric take-rates 20% / 10% | Per-platform `commission_fraction` | `MarketplaceParameters.commission_fraction` (`marketplace_policy.py` L68, L99); payout `rounded(gross * (1 - commission_fraction))` (L275); architecture formula in `plans/architecture/marketplace-policy.md` L210–215 |
| Drop third platform | `Scenario.remove` on platform table + segment edits | `platforms` is a `Table` (`scenario.py` L522); `remove` supports `Table` keys (L846–854); fixture already removes unused segments (`scenarios/fixture_cross_platform_queue.py` L30–32) |
| Multihoming access (both apps installed) | Segment / person `apps` | `PersonProfile.apps` / segments (`behavior_policy.py` L117–118; `_segments_v1` L651–662); compiler requires usable launched apps (`scenario.py` `_check_access` L1148–1173) |

A concrete authoring path (not executed here): start from `three-platform-day@1` or `@week`, set `world.horizon_hours=24`, counts 200/40, `remove("platforms", "flyt")`, replace segments so every rider/driver has only the two retained apps (e.g. map Alpha→`rebu`, Beta→`blot`), set `platforms.rebu.policy.parameters.commission_fraction=0.2` and `platforms.blot.policy.parameters.commission_fraction=0.1`, empty campaigns, and a weekly trip generator with `trips_per_rider ≈ 12` for ~0.5 requests/rider/hour over 24h (`count = round(trips_per_rider * n_riders)` at `scenario.py` L1368–1370). Map units are kilometres (`README.md`); treating “coordinate unit” as km is consistent with the engine.

**Assumption:** Scenario “Platform Alpha / Beta” are labels for two configured platform IDs (e.g. `rebu`/`blot`), not new platform identity types.

### 1.2 Exact settlement identity for take-rate differential (mostly)

Driver payout is frozen at offer time from gross fare and commission (`marketplace_policy.py` `dispatch` L275–278; bound in `marketplace_engine.py` `create_offer` / `Assignment` / `_settle` L949–956, L1026–1029, L1167–1168, L1243–1247). With identical gross and commissions 0.2 vs 0.1, intended net shares are `0.80×fare` vs `0.90×fare`, so Beta’s share is designed to be **12.5% higher** than Alpha’s (`0.90/0.80 = 1.125`).

Settlements and offers retain integer `driver_payout_minor` / `payout_minor` in the log and final checkpoint; offline metrics aggregate them (`metrics.py` L343–381). So the payout check is **observable from logs** without new instrumentation, at offer or settlement grain.

**Limit (rounding):** half-up integer rounding is applied independently to each platform’s payout. For some distances the realized ratio `beta_payout / alpha_payout` is not exactly `1.125` even though both use the documented formula (inspection calculation over 0.1–20.0 km steps found many non-exact ratios, e.g. distance 0.1 km → gross 215 → 172 vs 194). The scenario’s “exactly 12.5%” wording is therefore only guaranteed for distances where both rounded payouts preserve the ratio, or if the check is interpreted as “each side matches `(1−c)×gross`,” not “ratio of rounded integers equals 1.125.”

### 1.3 Physical queue slots and one-at-a-time service

The engine enforces a market-wide cap of **two unfinished accepted commitments** and **one physical service at a time** (`marketplace_engine.py` `MAX_COMMITMENTS = 2` L33; docstring L1–8 / README architecture table; acceptance fails with `no_free_slot` L1020–1021; `_accept` queues a second commitment without changing motion L1001–1004). Platforms cannot see competitor commitments when building candidates (`PlatformContext` comments L421–423; `marketplace-policy.md` L161–163).

Observable check *“no dispatch to a driver already serving the other platform without an open queue slot”* is **largely supported at acceptance**: a third acceptance is rejected. Offers may still be **created** while the driver serves another platform (documented and demonstrated by `scenarios/fixture_cross_platform_queue.py`). If “dispatched” means successful assignment, the engine check holds; if it means “never offered,” current policy deliberately allows cross-platform offers into a free slot.

### 1.4 Completed-ride counter consistency

`metrics.calculate_metrics` builds `platform_completions` by counting completed orders per `platform_id` and reports shares whose numerator sum equals the completed set size (`metrics.py` L354–382). Market-share periods do the same (`aggregate_market_share` L294–321). Runtime also increments per-platform `platform_memory[...]['completed']` on `order_completed` (`policy_runtime.py` L471–476). The scenario’s sum-of-platforms vs total check is therefore **measurable from current metrics/logs**.

### 1.5 Multihoming coexistence and simultaneous offers (engine level)

Competing platforms may send offers at the same simulated time; local “one pending offer per platform order/driver” does not block cross-platform concurrency (`marketplace-policy.md` L155–159; `MarketplacePolicy.dispatch` waits only on **own** pending offers L261–263). The discrete-event scheduler orders same-time work by FIFO sequence (`event_engine.py` L161–164, L179, L246). Driver responses are delayed by `response_seconds` then applied through `respond_to_offer` (`policy_runtime.py` L515–527, L420–438).

So the **marketplace can host** simultaneous multihoming offers and record resolve order. What it does **not** yet do is make the stock driver policy evaluate those offers atomically against each other (see §§2–3).

### 1.6 Approximations already expressible via traits (not exact scenario behavior)

- **Rider lowest-ETA / coin-flip:** `RiderPolicy.decide` maximizes a multi-attribute utility (price, ETA, loyalty, taste, search cost) with deterministic tie-break `(-utility, platform_id, -at, -id)` (`behavior_policy.py` L223–237). Setting `price_sensitivity=0`, `loyalty=0`, `taste_scale=0`, high `purchase_bias`, and equal fares **approximates** ETA-led choice, but equal-ETA ties are **not** a random coin flip; they sort by platform id / quote metadata. Docs recommend `taste_scale=0` for deterministic purchase tests (`behavior-policy.md` L101–102).
- **Drivers “both apps online” at t=0:** on `shift_started` only the preferred app is opened; other apps open after `no_offer_seconds` via expand (`policy_runtime.py` L506–514; `behavior-policy.md` L106–112; `DriverPolicy.expand` L262–279). `expansion='multi_app'` plus a very small `no_offer_seconds` approximates dual online quickly; it is not instantaneous dual broadcast at shift start.
- **Prefer higher net payout:** logistic acceptance rises with payout (`DriverPolicy.respond` L281–295). With high `acceptance_bias` / `payout_sensitivity`, higher-commission-share offers are **more likely** to be accepted, but each offer is scored alone—there is no forced “accept Beta, reject Alpha” when both are pending.
- **Operating cost $0.30/unit:** not represented as a vehicle cost in settlement or acceptance. Contribution “excludes operating costs/taxes” (`marketplace-policy.md` L255; `README.md` metrics text). Costs are therefore outside the shipped money model; they do not block the four observable checks if those checks ignore cost, but they do blunt the research framing of “net” earnings after operating cost.

---

## 2. What is almost possible with a small, quick, independent adjustment

### 2.1 Atomic prefer-Beta when two offers are pending (observable check #2)

**Gap:** `DriverPolicy.respond` receives a single `context.offer` and never compares other pending offers (`behavior_policy.py` L281–295). `driver_context` does not include `pending_offers` even though `DriverView.pending_offers()` exists (`policy_runtime.py` L198–204; `marketplace_engine.py` L549–551). Declaration fields omit pending offers (`behavior_policy.py` L253–256). With `MAX_COMMITMENTS=2`, an idle driver can **accept both** simultaneous offers (first fills slot 1, second still has a free slot), which contradicts “reject/ignore Alpha and accept Beta.”

**Smallest adjustment:**  
1. Pass `pending_offers=view.pending_offers()` (or freeze thereof) into `driver_context` for the respond hook and extend the declaration observation list.  
2. In `respond`, if another pending offer has strictly higher `payout_minor+bonus_minor` (or same payout and a configured platform preference / Beta id), return `accept=False` for the inferior offer; accept the superior one when its timer fires. Optionally treat “same matching tick” as pending offers whose `created_at` equals `now` or lie within a tiny epsilon of each other.

**Why small/independent:** localized to `policy_runtime.driver_context`, `DriverPolicy.respond`, and declaration metadata; engine acceptance and settlement unchanged.  
**Verify:** a minimal explicit two-order fixture (pattern of `fixture_cross_platform_queue.py`) that creates Alpha+Beta offers to one idle driver at the same second; assert offer dispositions Alpha rejected / Beta accepted in the log’s offers table and `policy_decision` records.

### 2.2 Open all usable apps at shift start (closer to “both apps online”)

**Gap:** only preferred app opens at shift start (§1.6).

**Smallest adjustment:** in `policy_runtime.on_notification` `shift_started` (L506–514), open every `usable_apps('driver', …)` (or all `apps` ∩ launched), not only preferred; keep expand for later exclusive-switch behavior. Alternatively add a `DriverTraits` flag `open_all_apps_on_shift: bool` default false for backward compatibility.

**Verify:** after `shift_started`, driver `open_apps` contains both platforms before any `no_offer_seconds` wait; both platforms can include the driver in `candidates` immediately (`MarketplacePolicy.candidates` requires participation L229–233).

### 2.3 Rider equal-ETA coin flip

**Gap:** utility tie-break is lexicographic, not stochastic (`behavior_policy.py` L232).

**Smallest adjustment:** when two viable quotes share the same ETA (and, if desired, same net price), break ties with `random.uniform(('eta_tie', …))` instead of `platform_id`. Keep other utility terms at zero via traits for the scenario.

**Verify:** paired quotes with identical `eta_seconds` and fares yield ~50/50 platform choice across seeds; unequal ETA still picks the lower ETA when price terms are disabled.

### 2.4 Clarify / soft the “exactly 12.5%” payout check for integer cents

**Gap:** independent rounding (§1.2).

**Smallest adjustment (docs or check helper, not necessarily engine):** define the check as `payout == round_half_up(gross * (1 - c))` per platform, and/or compare unrounded rational shares. Optionally add a tiny metrics helper that groups completed settlements by distance/gross and reports payout ratios. No marketplace formula change required if the scenario wording is relaxed.

---

## 3. What is not currently possible and what must be developed

### 3.1 “Wait for Beta instead of accepting immediate Alpha” option value

**Missing behavior:** when only Alpha offers, the driver should weigh waiting for a Beta offer against accepting Alpha now. Stock `respond` is a myopic logistic on the current offer’s payout and private ETA (`behavior_policy.py` L281–295). Evolution memory estimates per-platform wait (`estimated_wait_seconds` from offer rates, `EvolutionPolicy.checkpoint` L322–330; `behavior-policy.md` L154–157), but **respond never reads that memory**, and learning defaults to `learning_rate=0` (`EVOLUTION_DEFAULTS_V1` L630–633).

**What to develop:** either (a) extend `DriverPolicy.respond` (or a new hook) to compare current Alpha payout to expected Beta payout × survival of wait, using opportunity priors / learned waits, or (b) a custom registered driver policy (`README.md` / `policy_runtime.register_policy`) with its own parameters. Needs delayed rejection / deferred acceptance semantics so an Alpha offer can be held or declined while waiting—today decline is immediate and platforms retry other drivers (`MarketplacePolicy.dispatch` L264–278).

**Approximation loss if skipped:** supply will not systematically withhold from Alpha while idle for Beta; elasticity results will mix take-rate effects with independent accept noise and dual-accept queueing (§2.1), not pure take-rate reallocation.

**Success check:** instrumented offers where Alpha arrives alone → measurable reject-and-wait rate that rises with Beta’s historical offer rate / payout advantage; when Beta arrives within the wait horizon, Beta accept dominates.

### 3.2 True “matching tick” atomic multihoming arbiter (modeling challenge)

**Ambiguity / missing capability:** the scenario asks whether simultaneous dispatches are evaluated atomically across platforms or race by processing order. The engine is explicit FIFO-at-equal-time (`event_engine.py` L161–164). There is **no** cross-platform matching tick, barrier, or joint choice set. Platform dispatch hooks run independently when their orders are created (`policy_runtime` `order_created` → `queue_dispatch` L465–466).

Even after §2.1, comparison is still “when each respond event fires, look at currently pending offers,” which is sensitive to `response_seconds`, offer creation sequence, and expiry. A full answer to the modeling challenge needs a designed arbiter, for example: batch pending offers to a driver at the same `created_at` (or within a tick), decide once, apply accepts/rejects together—likely touching `policy_runtime` offer scheduling and possibly the scheduler contract.

**Unresolved scenario requirement vs limitation:** the DESCRIPTION poses this as the main modeling challenge/ambiguity rather than mandating one semantics. **Assessment:** today’s semantics are **order-of-execution / per-offer**, not atomic cross-platform evaluation. Implementing atomic ticks is new behavior, not a config switch.

### 3.3 Vehicle operating cost $0.30 per coordinate unit in net earnings

**Missing state:** no per-km operating cost on drivers, cars, or settlements. Driver reward uses payout / physical service seconds (`driver_reward` L384–385), not distance cost. Metrics contribution excludes operating costs by design.

**If the research question requires cost-adjusted net:** add a world/driver cost parameter, accumulate distance on service legs, and expose net = payout − cost in diagnostics/metrics (and optionally in acceptance utility). That is a new accounting concept across engine logging and metrics.

**If only the four observable checks matter:** this can stay out of scope; flag that “net payout” in checks means **post-commission fare share**, not post-cost profit.

### 3.4 Guaranteeing dual-app broadcast without policy change

Without §2.2 (or equivalent), configuring `no_offer_seconds=0` still depends on expand events and visited-app bookkeeping; it is not the same as both apps open in the same `shift_started` transition. Treating that residual as configuration-only would overclaim.

### 3.5 Custom policy route (escape hatch, still development)

The README allows registering custom policy classes selectable by scenarios, with the caveat that the compiler cannot prove correctness. Building a Scenario-1-faithful driver (and possibly rider) policy is feasible but is **new policy code**, not a preset knob—and respond still needs pending-offer observations (§2.1) unless the custom policy reaches into disallowed engine state (forbidden by the policy contract).

---

## 4. Documentation quality and code readability

### What helped

- Root `README.md` clearly maps files to responsibilities, states the two-commitment / one-service rules, logging schema, and offline metrics ownership—enough to know where to look before opening code.
- `plans/architecture/marketplace-policy.md` gives the normative fare/commission/settlement equations and explicitly documents simultaneous cross-platform offers and competitor-blind matching (L148–170, L206–217).
- `plans/architecture/behavior-policy.md` accurately describes preferred-app shift start, expand clocks, logistic acceptance, and that private ETA includes cross-app workload (L104–123)—this was decisive for separating “configured multihoming” from “both apps online + atomic payout compare.”
- `plans/architecture/scenario-definition.md` explains presets, dotted overrides, `add`/`remove`, and prepare/reuse—aligned with `scenario.py`.
- `scenarios/fixture_cross_platform_queue.py` is an excellent minimal example of cross-platform queueing and ETA underprediction; it anchors confidence on queue-slot behavior.
- Code is relatively compact and symbol-rich: frozen parameter dataclasses, explicit `CommandRejected` paths, and comments at hot spots (`MAX_COMMITMENTS`, platform context blindness) make inspection tractable.

### What hurt or was easy to misread

- Scenario “Alpha/Beta” names do not appear in the repo; reviewers must map them onto `rebu`/`blot`/`flyt` mentally. A short note in scenario-review DESCRIPTION or an example two-platform variant would reduce friction.
- “Both apps online” in the scenario text vs “preferred app then expand” in behavior docs is a **silent semantic gap**; without reading `policy_runtime` `shift_started`, one might assume install ⇒ receiving offers.
- Rider “pick lowest ETA (coin flip if equal)” is easy to confuse with the richer utility model; docs describe the utility clearly but do not call out coin-flip as unsupported.
- `DriverView.pending_offers` exists while the stock respond context omits it—discoverable only by cross-reading engine views vs `driver_context` / declaration fields.
- No unit tests (stated in README); confidence rests on fixtures and reading. Phase 3 “extensive scenario testing remains” is still accurate for this review case.
- Observable check #3’s verb “dispatched” is ambiguous relative to offer creation vs acceptance; docs are careful, the scenario text is not.

### Actionable suggestions (documentation / readability only)

1. Add a “Scenario authoring cookbook” snippet: two platforms, asymmetric `commission_fraction`, dual-app segments, 24h demand rate via `trips_per_rider`.  
2. Document that driver offer decisions are **per-offer FIFO**, not cross-platform atomic, and point to `event_engine` same-time ordering.  
3. Mention integer payout rounding when stating percentage differentials.  
4. Cross-link `DriverView.pending_offers` to the behavior contract as “available to custom policies / future hooks but not in `driver_participation@1` respond context.”  
5. In scenario-review `DESCRIPTION.md`, define Alpha/Beta ↔ platform ids and whether “dispatch” means offer or assignment.

### Separation

Documentation gaps above do **not** by themselves implement atomic waiting-for-Beta or operating costs; those remain functional holes (§3). Clarity would mainly prevent overstating what configuration alone can reproduce.

---

## Coverage checklist (material DESCRIPTION requirements)

| Requirement | Assessment |
| --- | --- |
| 24h, 200 riders, 40 drivers, 10×10 grid | Configurable (inspection) |
| Alpha 20% / Beta 10% take-rate, identical fares, no surge/promos | Configurable via `commission_fraction` + defaults |
| Riders multihome; lowest ETA; coin flip if equal | Multihome yes; ETA-led approximate via traits; coin flip needs small code (§2.3) |
| Drivers both apps installed & online; $0.30/unit cost | Installed yes; online-at-start approximate/needs tweak (§2.2); operating cost not modeled (§3.3) |
| Idle: both apps offer; simultaneous → prefer higher net (Beta) | Offers can coexist; prefer-Beta atomic choice **not** in stock policy (§2.1 / §3.2) |
| Only Alpha → weigh waiting for Beta | **Not** in stock respond (§3.1) |
| Check: Beta payout 12.5% higher same distance | Formula yes; exact ratio subject to cent rounding (§1.2 / §2.4); measurable |
| Check: same tick → reject Alpha, accept Beta | **Not** guaranteed today; small policy+context change (§2.1) |
| Check: no assign without free queue slot while serving other | Engine acceptance yes; offers still allowed (`fixture_cross_platform_queue.py`) |
| Check: completed rides sum consistency | Supported by `metrics.py` |
| Challenge: atomic vs race/order | Current: race/FIFO per offer; atomic arbiter would be new (§3.2) |

**Uncertainty:** no end-to-end Scenario 1 run was executed; conclusions about measurability assume the documented JSONL checkpoint/offer/settlement fields appear as in `README.md` / `metrics.py`. Exact trip-rate calibration (`trips_per_rider` vs “0.5 per hour”) depends on horizon and peak weights and was not numerically validated in a prepared `Inputs` object.
