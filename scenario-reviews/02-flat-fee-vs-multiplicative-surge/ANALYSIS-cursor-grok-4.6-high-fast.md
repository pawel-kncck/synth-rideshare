# Scenario 2 review: Flat-Fee Bonus vs. Multiplicative Surge Under Distance Heterogeneity

- **Full model identifier:** `cursor-grok-4.6-high-fast`
- **Review date:** 2026-09-08
- **Repository revision:** `28e839eae51358d7646ae0a5f156508ba30b1c11` (`28e839e Add monthly market-share scenario and LLM review cases`)
- **Working-tree state:** clean on `cursor/scenario-02-analysis-bec2` branched from `origin/main` at the revision above; this file is the only review deliverable.
- **Evidence basis:** inspection of execution paths in `scenario.py`, `marketplace_policy.py`, `marketplace_engine.py`, `behavior_policy.py`, `policy_runtime.py`, `main.py`, `metrics.py`, `policy_contracts.py`, and `plans/architecture/*`. A **compile/prepare probe** (not a full run) was executed: isolated `MarketplacePolicy.quote` calls plus `compile_scenario` / `Plan.prepare(seed=1)` of a two-platform Metro/Urban definition. Observed outputs are reported below. No other models' `ANALYSIS-*.md` reports were used.

Assumptions used throughout: `t` is hours after the calendar origin; coordinate “units” are the engine’s Cartesian kilometres (`README.md` “Sessions, time and checkpoints”); money uses the preset `minor_units_per_major = 100`; “Metro” / “Urban” may be new platform IDs (verified) or aliases of built-in `rebu` / `blot`; instantaneous deactivation applies to **new** quote calculations, not already-issued quotes; demand is an exogenous schedule, not an emergent response.

---

## 1. What is currently possible

### 1.1 Two named platforms, 6-hour horizon, 150 riders / 30 drivers

The compiler accepts an arbitrary `platforms` table (`scenario.py` `PLATFORM` / `SCENARIO` around lines 480–529). `MarketplaceEngine.add_platform` stores whatever string ID is supplied (`marketplace_engine.py` 674–678). A 6-hour horizon is `world.horizon_hours`. Population counts are `population.riders.count` / `population.drivers.count`. Segment weights with largest-remainder assignment produce exact 75/75 splits.

**Probe:** after `remove("platforms", {rebu,blot,flyt})` and replacing preset segments, `compile_scenario` yielded platforms `['metro', 'urban']`, `horizon_seconds = 21600`, 150 riders / 30 drivers, and `Counter({'long': 75, 'short': 75})`. Preset segments **merge** rather than replace (`Table.merge` in `scenario.py` 227–233); leftover `rebu-first` etc. must be `remove`d or the compiler reports unknown platform IDs and weights summing to 2.0.

Drivers can be declared multihoming: `apps=("metro","urban")`, `registrations=("metro","urban")`, `expansion="multi_app"` (`DriverTraits.expansion`, `behavior_policy.py` 76–77; default `DRIVER_DEFAULTS_V1` at `scenario.py` 625). That is **access** multihoming. Simultaneous open-app participation from `t = 0` is not the default (see §2.3 and §3.4).

### 1.2 Shared baseline tariff $3.00 + $1.00/km

`MarketplaceParameters` exposes `base_fare_minor`, `per_km_minor`, `per_minute_minor`, `minimum_fare_minor`, `multiplier` (`marketplace_policy.py` 62–67). The shipped quote is

```text
G = max(minimum_fare, multiplier * (base + per_km * km + per_minute * minutes))
```

implemented at `MarketplacePolicy.quote` (`marketplace_policy.py` 243–256). With `per_minute_minor = 0` (the default, `scenario.py` 636), Euclidean distance is the only quantity that scales the fare.

**Probe:** `base_fare_minor=300`, `per_km_minor=100`, `multiplier=1.8`, origin/destination 1 km and 10 km apart:

| quote | `gross_minor` | distance |
| --- | --- | --- |
| Metro 1 km | 720 ($7.20) | 1.0000 |
| Metro 10 km | 2340 ($23.40) | 10.0000 |
| Urban base 1 km | 400 ($4.00) | 1.0000 |
| Urban base 10 km | 1300 ($13.00) | 10.0000 |
| Urban `base+500` 1 km | 900 ($9.00) | 1.0000 |
| Urban `base+500` 10 km | 1800 ($18.00) | 10.0000 |

Those are exactly the numbers in the scenario’s second observable check, **if** Metro’s multiplier is on and Urban’s rider fare includes a flat +$5. Rounding is decimal half-up (`rounded`, `marketplace_policy.py` 15–16); 1.8 × 400 and 1.8 × 1300 are exact integers.

### 1.3 Time-boxed, platform-wide tariff changes (approximation of the surge window)

There is no automatic surge controller. `MarketplacePolicy.controller` always returns `Stop('fixed policy')` (`marketplace_policy.py` 302–306). Architecture states this explicitly: “Automatic surge … remain extensions rather than advertised implementations” (`plans/architecture/marketplace-policy.md` 96–98, 117–119).

What **is** implemented:

- `policy_change` interventions select a new compiled `PlatformPolicy` at a simulated time (`scenario.py` 600–603, 980–985; applied in `PolicyRuntime._intervene`, `policy_runtime.py` 622–639). Omitted parameters merge with the platform’s current definition, so a change that sets only `multiplier: 1.8` keeps `base_fare_minor` / `per_km_minor`.
- Campaign windows are half-open `[start, end)` (`Campaign.eligible`, `marketplace_policy.py` 51–54; compiler converts `start_hours`/`end_hours` to seconds at `scenario.py` 1136–1140).

**Probe:** interventions compiled to `metro-surge-on` at 7200 s with `multiplier=1.8` and still `base_fare_minor=300`; `metro-surge-off` at 14400 s with `multiplier=1.0`; Urban `base_fare_minor` 800 → 300 at the same instants. Urban campaign `flat-pass-through` compiled to `[7200, 14400)` with `bonus_minor=100`.

At `t = 4.01` (14436 s) any intervention scheduled at `t = 4.0` has already been applied (the scenario’s 4.01 check sits safely after the half-open end). New quotes then use the restored base parameters. Committed quotes/orders are **not** repriced (`plans/architecture/marketplace-policy.md` 240–244; README “Existing quote and accepted offer terms remain committed”). Default `quote_seconds = 30`, so a quote issued at 3:59:50 still carries surge terms until ~4:00:20.

This is **platform-wide**, not origin-conditional. Every ride quoted during `[2, 4)` would surge, including those originating outside `(4,4)–(6,6)`.

### 1.4 Driver-side flat bonus (part of Urban’s “pass 100% of the fee through”)

Campaigns can pay a fixed `bonus_minor` on completion; a bonus does not increase rider payment (`marketplace_policy.py` 273–278; `plans/architecture/marketplace-policy.md` 228–230). Combined with a rider-fare increase of 500 minor units and default `commission_fraction = 0.2`:

```text
Δdriver = (1 − c) × 500 + bonus = 400 + bonus
```

`bonus_minor = 100` makes the driver receive exactly +$5.00 relative to the unsurged payout. The scenario does not state a take-rate; this reconstruction assumes the shipped 20% commission on gross (`MARKETPLACE_DEFAULTS_V1`, `scenario.py` 636–637) and treats “100% of this fee” as the incremental $5, not the whole fare.

A campaign cannot represent a rider **surcharge**. `discount_minor` is `minimum=0` (`scenario.py` 472–473). **Probe:** `campaign(..., discount_minor=-500)` raised `ScenarioError: ... must be at least 0`.

### 1.5 Exogenous trips, shifts, and a global time peak

Activity is exogenous (`plans/architecture/scenario-definition.md` 155–160: “Endogenous demand … are not implemented”). Two usable encodings:

- `trips.generator = "weekly"` with a peak `{start_hour: 2, end_hour: 4, multiplier: 2}` doubles **clock-hour arrival weight globally** (`_peak_weight` / `_sample_times`, `scenario.py` 1389–1418). Origins and destinations are independent uniform draws on the map (`_realize_activity`, 1368–1379). There is no per-segment distance and no spatial intensity.
- `trips.generator = "explicit"` with `trip(...)` items can pin rider, time, origin, and destination (`scenario.py` 592–593, 1380–1383). That is enough to author 75 short-haul (1–3 km) and 75 long-haul (8–12 km) paths, a doubled count of zone-origin intents in `[2, 4)`, and the exact 1 km / 10 km exemplars used in the quote check.

**Probe (weekly, 12×12 km grid, peak ×2 on clock hours 2–4):** 150 trips; 67 in `[2, 4)`; only 12 with origin in `[4,6]×[4,6]`; 8 in both; distances `[1.00, 6.32, 16.97]`; 24 trips in `[1,3]` km and 41 in `[8,12]` km, **not** bound to the short/long segments. A weekly generator therefore cannot reproduce the intended distance heterogeneity or localized demand shock.

Shifts: `rotation` with `crews=1`, `shift_hours=6` keeps 30 drivers on for the horizon; start locations are `_sample_point` draws (`scenario.py` 1351–1360). **Probe:** 30 shifts, 28 unique start cells — consistent with “approximately uniform,” not a guaranteed lattice.

Default map is 10×10 km (`scenario.py` 669). Long-haul 12 km trips fit on the diagonal of 10×10 or on a larger `world.map_km` (12×12 was accepted). The surge box `(4,4)–(6,6)` is inside either map. Inclusive bounds are an assumption; the text does not say whether the box is closed.

### 1.6 Riders can see two final prices and an ETA, and choose among observed quotes

Riders open apps, receive frozen `Quote` records (`FareTerms.gross_minor` / `discount_minor`, `eta_seconds`, `distance_km`; `marketplace_engine.py` 139–153, 251–264, 880–908), and `RiderPolicy.decide` scores every still-valid quote with both net price and pickup ETA (`behavior_policy.py` 210–237). The purchase utility is

```text
purchase_bias − price_sensitivity × net_price / route_reference
− eta_sensitivity × eta / eta_tolerance
+ loyalty × preferred + learned_score − search_cost × visit_index
+ taste_scale × logit(taste)
```

(`plans/architecture/behavior-policy.md` 78–91). That **does** evaluate non-linear (distance-dependent) price gaps, because the gap is already in the quoted money. It does **not** implement “cheaper wins; if within $0.50 use lower ETA.” Price and ETA always enter as one linear combination with a single `price_sensitivity`. Search is sequential (preferred app first, then inspect), gated by `acceptable_price_ratio` and a reused inspection draw (`behavior_policy.py` 214–221). Defaults (`scenario.py` 617–622) allow up to three app visits, so both platforms **can** be seen, but a rider who finds the first quote “acceptable” may never open the second.

Trait knobs that push the built-in model toward “compare both, mostly pick cheaper”:

- `taste_scale=0`, `loyalty=0`, `search_cost=0` (documented for deterministic tests, `behavior-policy.md` 101–102)
- low `acceptable_price_ratio` so inspection is almost certain
- high `price_sensitivity` / low `eta_sensitivity`

Those remain an approximation of the $0.50 deadband (see §2.4).

### 1.7 Drivers receive competing platforms’ offers independently; reject does not lock Urban

Engine invariants that match the “no phantom locks” check:

- Offers “reserve nothing globally” (`create_offer`, `marketplace_engine.py` 952).
- `DriverPresence.pending_offer_ids` is **this platform’s** pending offers only (445–454). Metro’s `candidates()` cannot see an Urban pending offer (`marketplace_policy.py` 227–232).
- `respond_to_offer(accept=False)` calls `_resolve_offer(..., "rejected")`, which drops the offer from `_pending_by_driver` and notifies `offer_resolved` / `offer_closed` (998–1014, 1046–1056).
- `close_app` cancels only that platform’s pending offers; accepted orders survive (814–827).
- The market-wide cap is two accepted commitments, not two pending offers (`MAX_COMMITMENTS`; `marketplace-engine.md` 28–29).
- After reject, Urban will not re-offer the same driver unless `retry_drivers=True` (default `False`, `marketplace_policy.py` 267–268). That is a retry policy, not a lock.

So a driver who rejects Urban and later accepts Metro does not remain pending or “accepting=false” on Urban. The check is evaluable from the final snapshot’s `offers` table and `offer_resolved` notifications.

What is **not** currently possible is the **decision** “reject Urban short **in order to** take Metro long.” `DriverPolicy.respond` sees one offer and a logistic of **total** payout versus **pickup** delay (`behavior_policy.py` 281–295). `driver_context` does not pass `DriverView.pending_offers()` (`policy_runtime.py` 198–204 vs `marketplace_engine.py` 549–551). Each offer is accepted or rejected independently after `response_seconds` (default 3).

### 1.8 Observability of the four checks

`Simulation.run` writes one JSONL log: notifications plus initial/final snapshots (`README.md` “Raw log and offline metrics”; `main.py` 170–225). The final snapshot includes every `Quote` (fare, distance, `at`, `policy_version`, `selected_rule`, `campaign_id`), `Offer` (payout, bonus, state, timestamps), `Order` / `Settlement`, policy `decisions`, and `observations` including `type=intervention` (`policy_runtime.py` 126–134, 639, 682–687).

`metrics.py` reconstructs searches, conversions, offer dispositions, and money totals. It does **not** emit quote-vs-distance, Metro-vs-Urban price order, phantom-lock, or post-4.01 tariff checks (`collect_metric_records`, 251–291). Those must be computed offline from the snapshot. `quote_received` notifications carry only `quote_id` / `platform_id` / `intent_id` (`marketplace_engine.py` 906–907); the fare lives on the quote record, not the notification.

---

## 2. What is almost possible with a small, quick, independent adjustment

Each item below is a localized hook or generator change. None requires a new engine record type, a batch experiment runner, or a rewrite of settlement.

### 2.1 Origin-and-time surge inside `MarketplacePolicy.quote`

**Gap.** `quote` already reads `context.request.origin` and `context.now` but never uses the origin for price (`marketplace_policy.py` 243–249). `ConditionalRule` may only match `role`, `segment`, `new_user`, `completed_rides` (`VISIBLE_FIELDS`, 108–122). **Probe:** `rule(..., when={"origin_in_zone": True}, ...)` passed the scenario builder (`RULE.when` is an unrestricted `Table`) but `PlatformPolicy.compile` raised `ValueError: Conditions must use declared platform-visible fields`.

**Smallest adjustment.** A registered custom marketplace implementation (supported: `register_policy`, `policy_runtime.py` 35–53; parameter schema may **subclass** `MarketplaceParameters`, line 43) that, in `quote` only:

- if `now ∈ [t_start, t_end)` and origin ∈ `[4,4]–[6,6]`, set Metro `multiplier = 1.8` or add Urban `+500` to gross;
- leave dispatch/revise/cancel unchanged.

Alternatively, add optional `surge_box`, `surge_start`, `surge_end`, `surge_flat_minor` fields on a `MarketplaceParameters` subclass and keep `marketplace@1` as the fallback. No engine change. Verify by issuing quotes for (zone, in-window), (zone, after 4.0), and (outside zone, in-window) and checking `gross_minor`.

This is the smallest way to make the first two observable checks true for a mixed spatial demand pattern. Using `policy_change` alone cannot restrict surge to the box.

### 2.2 First-class rider addend (Urban’s +$5 as a fee, not a base-fare mutation)

**Gap.** Gross is `multiplier * raw`. There is no `surcharge_minor`. Raising `base_fare_minor` by 500 during the window is a working approximation for **quotes** (probe §1.2) but conflates “flat surge fee” with “new base fare,” complicates commission accounting, and still is not origin-conditional.

**Smallest adjustment.** One extra nonnegative integer on the parameter object, applied after `raw` and before or after `multiplier` (the scenario wants Urban: `G = raw + 500`, Metro: `G = 1.8 * raw` — they must not share one formula path). Pair with `bonus_minor = commission_fraction * 500` or a dedicated “pass-through fee” so driver payout is `(1−c)×raw + 500`. Verify on 1 km and 10 km quotes plus the matching `OfferProposal.payout_minor` / `bonus_minor`.

### 2.3 Open every usable driver app at shift start

**Gap.** On `shift_started`, the runtime opens only the preferred app (`policy_runtime.py` 506–510). Further apps open only after `no_offer_seconds` without an offer (`DriverPolicy.expand`, `behavior_policy.py` 262–279). **Any** offer receipt resets `no_offer_since` (`policy_runtime.py` 515–517), including a rejected Urban short. During a spike, drivers who are receiving Urban offers may never open Metro, so the “reject Urban / take Metro” path cannot occur.

**Smallest adjustment.** Either (a) in the `shift_started` handler, open all `usable_apps`, or (b) a custom `DriverPolicy.expand` that returns `ExpandApps` immediately for remaining apps (or a trait `open_all_at_start`). No engine change. Verify: after `shift.start`, both `metro` and `urban` appear in `driver.open_apps` before the first offer.

### 2.4 Lexicographic rider rule (price, then $0.50 ETA tie-break)

**Gap.** The built-in utility always mixes price and ETA with one sensitivity (`behavior_policy.py` 223–237). That is exactly the modeling challenge in `DESCRIPTION.md`: whether choice can parse structural price differences **together with** ETAs rather than through “a single uniform sensitivity parameter.” The current model is the latter. Sequential search can also skip the second app (§1.6).

**Smallest adjustment.** A custom `RiderPolicy.decide` (same `RiderTraits` schema, new `name@version`) that, after visiting both usable apps (or forcing `max_app_visits` / inspection), chooses `argmin` price and, if `|p1−p2| ≤ 50` minor units, `argmin` `eta_seconds`. Keep `OpenApp` / `OrderQuote` / patience budgets. The registry already allows this. Verify with two simultaneous quotes at ($7.20, long ETA) vs ($9.00, short ETA) and at equal prices.

This is independent of surge implementation: it only needs two valid quotes.

### 2.5 Comparative driver response using already-collected pending offers

**Gap.** Drivers cannot compare Urban vs Metro because `respond` is one-offer and the context omits `pending_offers` (§1.7). The stated rule is “maximize immediate expected net payout per minute.” The shipped score uses **total** payout / `reference_payout_minor` and **pickup** delay, not trip duration (`behavior_policy.py` 283–287). Learning uses `payout / physical_service_seconds` (`driver_reward`, 384–385) but that is not the acceptance rule.

**Smallest adjustment.** Add `pending_offers=view.pending_offers()` to `driver_context` (one argument; the view already exists) and a custom `respond` that (i) computes `driver_payout / expected_minutes` for the current offer and any other pending offer, (ii) accepts the best and rejects the rest (or rejects the current if a better pending exists). Expected minutes can use the offer’s destination plus `world.speed_kmh` already available to the runtime’s `private_eta` helper (`policy_runtime.py` 413–418).

Verify with two overlapping pending offers: Urban 1 km vs Metro 10 km, and confirm dispositions plus empty Urban pending after reject.

### 2.6 Spatial intensity on the weekly sampler (optional, if explicit trips are declined)

**Gap.** Peaks weight time only (`scenario.py` 1389–1418). `_sample_point` is spatially uniform (1336–1341).

**Smallest adjustment.** Extend `PEAK` or add a parallel `spatial_peaks` list `{box, start_hour, end_hour, multiplier}` and, when sampling an origin, overweight the box during the window. Independent of pricing. Verify prepared-input counts: zone-origin share in `[2, 4)` ≈ 2× the off-window zone share.

Explicit trips already achieve the same without code (§1.5); this item is only needed if the experiment wants a generator rather than a fixture.

### 2.7 Tiny analysis helpers (not simulator features)

The four observable checks can be answered from the existing log with a throwaway script: join quotes by `intent_id` and `distance_km`; compare `gross_minor` across platforms in `[7200, 14400)` vs `≥ 14436`; walk each driver for `rejected` Urban then `accepted` Metro with no leftover Urban `pending`. That is analysis, not a product change.

---

## 3. What is not currently possible and what must be developed

If a requirement needs more than a single hook or already-supported custom policy, it is here. Approximations from §1 that lose part of the requirement are restated.

### 3.1 Automatic, localized surge as a built-in commercial model

The architecture lists automatic surge as a non-shipped extension (`marketplace-policy.md` 96–98). There is no demand-triggered engine: platforms do not observe a “central zone is 2× busy” signal and flip a surge switch. The scenario’s wording — “Platform Metro **triggers** a 1.8× multiplier … for all rides originating in the zone” — can be read as (A) an exogenous timed/zonal rule or (B) an endogenous response to the demand doubling.

- **(A)** is §2.1 (small policy).
- **(B)** requires new platform observations (zone-level unmatched demand or quote volume), controller memory, and activation/deactivation policy. That is a new marketplace subsystem, not a parameter. The shipped controller refuses any non-`Stop` action (`marketplace_policy.py` 305–306; `policy_runtime.py` 595–596).

This review treats (A) as the intended mechanism because the times `t = 2` and `t = 4` / `4.01` are hard-coded. If (B) is required, it is undeveloped.

### 3.2 Endogenous “demand doubles in the zone”

Trip intents are scheduled before the run (`Plan.prepare`). Nothing in `main.py` or `behavior_policy.py` creates extra intents because wait times rose or because a zone is cheap. `scenario-definition.md` 155–160 and 209–210 forbid precomputing or inventing endogenous arrivals. A true demand-response model would need a new activity generator or rider-intent policy and a definition of the baseline rate being doubled. The text does not specify that baseline (zone share before `t = 2`, or market-wide?). Unresolved scenario requirement, not just a missing parameter.

Approximation: author more explicit zone-origin trips in `[2, 4)`. That reproduces the **schedule**, not an elastic response.

### 3.3 Short-haul vs long-haul as a generated distance process

Segments can change traits and app sets, not trip geometry. Weekly OD is independent of segment (`scenario.py` 1372–1379). There is no `distance: {"uniform": [1, 3]}` on a segment. Without explicit trips or a new sampler, the 75/75 rider groups will not request 1–3 vs 8–12 km trips (probe: 24 and 41 such trips, uncorrelated with segment). Developing a distance-conditioned activity generator is a scenario-compiler feature, not a one-line override.

### 3.4 Built-in simultaneous comparison markets (rider and driver)

Even with §2.4–2.5, the **productized** models would still be:

- Riders: one-at-a-time app opening with `opening_seconds` / `decision_seconds` delays (`policy_runtime.py` 303–307, 481–488). “Compare final quoted prices **across both apps**” as a single simultaneous screen is not how `RiderPolicy` or the runtime work. A rider can approximate it by visiting both apps before ordering; they cannot receive two quotes from one `OpenApp`.
- Drivers: platforms pick `nearest` / `pickup_eta` (`marketplace_policy.py` 102–103, 238–240) and send one local offer at a time. Drivers do not browse a cross-platform menu of “short Urban vs long Metro.” Broadcast dispatch is an explicit non-feature (`marketplace-policy.md` 157–159).

A first-class “compare all open-app quotes/offers now” behavior family would be new policy + runtime context, not a preset.

### 3.5 The stated driver objective vs the stated swap (unresolved)

“Maximize immediate expected net payout **per minute**” applied to Urban’s +$5 on a 1 km trip (≈2 minutes at 30 km/h) yields a very high $/min; Metro’s 10 km 1.8× fare yields more **total** dollars and lower $/min. The observable story — reject Urban **short** to take Metro **long** — matches the shipped **total-payout** logit better than a strict per-minute rule.

This is an unresolved scenario contradiction, not an implementation bug. A custom per-minute policy (§2.5) may **fail** the example swap. Success criteria should pick one: the verbal objective, or the example reject/accept pair.

### 3.6 Built-in metrics and “consistently cheaper” as a market outcome

`metrics.py` will not, without new aggregations, report “Metro consistently cheaper than Urban for 1-unit trips between t=2 and t=4.” The quote **structure** is a property of `MarketplacePolicy` and can be checked on individual quotes. “Consistently cheaper” as a **choice** outcome also depends on riders seeing both quotes (§1.6, §3.4) and on supply (`eta_seconds is None` quotes are dropped from `viable`, `behavior_policy.py` 211–212). Empty-supply quotes cannot enter the price comparison.

There is no unit-test suite (`README.md`); validation is scenario runs and throwaway scripts. A dedicated check harness would be new work.

### 3.7 What the closest configuration-only approximation loses

| Requirement | Closest configuration | What it loses |
| --- | --- | --- |
| Metro 1.8× on **zone** origins in `[2, 4)` | `policy_change` `multiplier=1.8` at t=2, revert at t=4 | Surges **all** origins; not localized |
| Urban rider +$5, 100% to driver | `base_fare_minor += 500` + `bonus_minor = c×500` campaign `[2,4)` | Same global scope; fee is not a distinct line item; depends on assuming `c` |
| Demand ×2 in the box | weekly peak ×2 **or** extra explicit trips | Peak doubles **everywhere**; explicit trips are a fixture, not a process |
| Short vs long haul groups | two segments + **explicit** OD | Weekly sampling will not sort distances by segment |
| Cheaper, else ETA within $0.50 | high `price_sensitivity`, low `eta_sensitivity`, force two visits | No deadband; loyalty/taste/search still present unless zeroed; sequential |
| Drivers max $/min across apps | high `payout_sensitivity`; `multi_app` | Total $ not $/min; no comparison; second app may stay closed |
| Instant off at 4.01 | campaign end / `policy_change` at 4.0 | Live quotes keep old terms until expiry |
| No phantom lock | already true on reject | Does not create the reject-to-switch **behavior** |

---

## 4. Documentation quality and code readability

### What made the assessment possible

- Root `README.md` maps files to responsibilities and states that quotes freeze, campaigns are half-open, and metrics are offline. That prevented treating `metrics.py` as a live dashboard.
- `plans/architecture/marketplace-policy.md` is the single most important document for this review: the fare equation (208–218), the explicit “automatic surge … remain extensions” sentence (96–98), and “a driver bonus does not increase rider payment” (230) separate configurable tariffs from the missing surge engine.
- `plans/architecture/behavior-policy.md` publishes the rider and driver equations and the sequential-search contract. The modeling challenge in `DESCRIPTION.md` can be compared to those equations without guessing.
- `plans/architecture/scenario-definition.md` states exogenous demand and “Do not precompute … dynamic surge, or endogenous arrivals” (209–210). That is the right warning for “demand doubles in the zone.”
- Executable paths are short and readable. `MarketplacePolicy.quote` (243–256) is the whole pricing model. `Campaign.eligible` (51–54) is the whole time window. `VISIBLE_FIELDS` (108) is the whole condition language. `DriverPresence` comments (406–407, 453–454) make the no-phantom-lock conclusion safe.

### Where documentation is missing, misleading, or scattered

- Narrative docs speak as if the only platforms are Rebu, Blot, and Flyt (`README.md` opening; `marketplace-engine.md` 7–8). IDs are actually free strings. A two-platform Metro/Urban example does not exist; `scenarios/blot_discount_week.py` is the closest (campaign + later `policy_change` on `per_km_minor`) and is a good template once preset platforms/segments are removed.
- “Multiplier” means three different things: fare `MarketplaceParameters.multiplier`, weekly demand `peak.multiplier`, and (in docs) surge. The probe used both in one scenario; that collision should be named in `scenario-definition.md`.
- Campaigns are documented as discounts and **driver** bonuses. Nothing says in one sentence “there is no rider surcharge / flat fee field.” A reader could take Urban’s “flat +$5 surge fee” to be `bonus_minor` and miss that riders would still see the unsurged fare.
- `rule()` in `scenario.py` 559–560 accepts any `when` keys; `PlatformPolicy.compile` then rejects unknown fields. The builder and the binder disagree. That should be the same schema, or the builder should use `VISIBLE_FIELDS`.
- `Table.merge` on `population.*.segments` and `platforms` is easy to miss. `with_changes({"population.riders.segments": {...}})` does not replace the preset mix; the probe’s first compile failed with weights 2.0 and unknown `rebu`/`blot`/`flyt`. The README’s “maps merge by schema” is accurate but not illustrated for “replace the market.”
- Driver start-up (preferred app only; offers reset the expansion clock) is specified in `behavior-policy.md` 105–113 and 280–281, but “multihoming” in a scenario brief is easy to over-read as “both apps open.” A one-line limitation next to `expansion="multi_app"` would help.
- No worked numeric example of `multiplier` on a quote appears in the README (defaults are $2.00 + $1.50/km × 1.0). The scenario’s $3 + $1 × 1.8 numbers are not in-repo; they had to be computed from source.
- `notes.md` still talks about “Rebu offers…” and report files that the current metrics CLI does not write. Treat it as stale relative to `README.md`.

### Readability of code vs confidence

Confidence is high on pricing, freeze/commit, reject-clears-pending, intervention timing, and activity sampling, because those are small functions with no hidden branches. Confidence is medium on “what a default rider will actually do with two surged quotes,” because `RiderPolicy.decide` mixes inspection, taste draws, outside option, and visit order — the equation is documented, but outcome frequencies need a run. Confidence is low on whether authors are expected to register custom policies for this scenario: the registry exists and is documented (`README.md` 86–90), but every published example uses `marketplace@1` / `rider_search@1` / `driver_participation@1`.

Actionable doc/readability suggestions (docs only; not implemented here):

1. In `marketplace-policy.md`, add a short “Not in this release” list: geographic/time surge, rider surcharge, spatial demand, comparative cross-platform driver choice.
2. Show a two-platform `remove` + new IDs snippet beside the Blot campaign example.
3. Tighten `rule()` validation to `VISIBLE_FIELDS` so origin conditions fail at authoring time.
4. Name the three “multiplier” meanings in `scenario-definition.md`.
5. State that `quote_received` notifications omit money; quote structure lives in the snapshot `quotes` table.

---

## Ambiguities and contradictions (not silently rewritten)

1. **Driver objective vs example swap** — max $/min favors Urban shorts; the lock check describes rejecting those for Metro longs (§3.5).
2. **“Triggers” surge** — timed exogenous rule vs endogenous controller (§3.1). This review assumes exogenous `t ∈ [2, 4)`.
3. **Demand doubling baseline** — vs the same zone off-peak, or vs the rest of the map? Not specified.
4. **Take-rate** — unspecified; default 20% was assumed for “100% of the fee to the driver.”
5. **Zone inclusivity** and whether non-zone trips continue during the spike (the word “localized” suggests they do).
6. **`t = 4.01`** — consistent with half-open `[2, 4)` plus a safety epsilon; not a third surge window.
7. **Modeling challenge vs stated rider rule** — the rule compares **final** prices (structure already baked in); the challenge asks whether logic can parse **structure** jointly with ETA instead of one sensitivity. The shipped model compares finals with one sensitivity. Those are different bars.
8. **“Units”** — treated as kilometres. If they were dimensionless grid steps on a non-1 `grid_step_km`, distances would change.

---

## Probe recap (what was run)

Not a full `Simulation.run`. Commands were local `python3` snippets importing `scenario` and `marketplace_policy`:

1. Isolated `MarketplacePolicy.quote` for Metro `multiplier=1.8` and Urban `base_fare_minor` 300 vs 800 at 1 km and 10 km — exact $7.20 / $23.40 / $9.00 / $18.00.
2. `compile_scenario` + `prepare(seed=1)` of Metro/Urban, 6 h, 150/30, 75/75 segments, weekly peak ×2, four `policy_change`s, one Urban bonus campaign — compile succeeded; weekly OD did **not** localize demand or distances.
3. Negative `discount_minor` rejected; origin `when` rejected at `PlatformPolicy.compile`.

Conclusions about runtime choice, locks during overlapping offers, and post-4.01 live quotes are from inspection of those paths, not from a logged run.
