# Scenario 8 review: Mid-Trip Regulatory Price Cap and Contract Immutability

- **Full model identifier:** `cursor-grok-4.6-high-fast`
- **Review date:** 2026-09-08
- **Repository revision:** `28e839eae51358d7646ae0a5f156508ba30b1c11` (`main`, clean working tree at review start; this file is the only intended diff)
- **Method:** Inspection of scenario docs, architecture notes, and execution paths in `scenario.py`, `main.py`, `policy_runtime.py`, `marketplace_policy.py`, and `marketplace_engine.py`, plus two throwaway `/tmp` fixtures (not checked in). Conclusions about contract binding are based on both inspection and those runs. The full 24-hour, 250-rider scenario was compiled but not executed.

This review treats [DESCRIPTION.md](DESCRIPTION.md) as requirements to evaluate, not as shipped behavior.

## Assumptions and ambiguities (do not silently rewrite the scenario)

1. **Clock notation.** DESCRIPTION writes `t = 11.45`, `t = 11.58`, `t = 12.20`. The repository authors times as hours after the calendar origin (`scenario.py` `HOUR = 3600`, `trip(..., at_hours=...)`). `11.45` hours is 11:27, not 11:45. The same numerals also look like European `HH.MM` clock times (11:45 / 12:20). The 10-minute pickup / 25-minute trip spacing is much cleaner under the clock reading. This review treats the *phase* of each boundary trip as the requirement (in transit / en route / post-shock) and treats the exact minute stamps as ambiguous.
2. **“Per unit”.** The engine prices in kilometres (`MarketplaceParameters.per_km_minor`, `math.dist` in `MarketplacePolicy.quote`). This review assumes “unit” means a kilometre. If it means a fare block, zone, or minute, that unit does not exist.
3. **Cap versus replacement tariff.** DESCRIPTION asks for a maximum of $2.00 per distance unit with “base fare locked at $2.50”. The built-in formula is `G = max(minimum, multiplier * (base + per_km * km + per_minute * minutes))` (`marketplace_policy.py` `MarketplacePolicy.quote`). `$2.50 + $2.00/km` is not the same as “no more than $2.00 per km”. Pre-shock “$4.00 per unit” does not state a base. The two readings conflict on any trip whose distance is not 1. This review assumes the experiment wants a *stated* pre-shock tariff of $4.00/km (base $0) and a *stated* post-shock tariff of $2.50 + $2.00/km, not a clamp that leaves platforms free to choose any lower price.
4. **“Dispatched”.** The engine has no dispatch timestamp distinct from offer acceptance. Fare is frozen at `issue_quote` / copied at `place_order`; driver payout is frozen at `create_offer` / bound at `_accept`. This review treats DESCRIPTION’s “dispatched” as assignment (`Order.timeline["assigned"]` / `Assignment.accepted_at`).
5. **Platform names.** DESCRIPTION’s Apex / Zenith are labels. The presets ship `rebu` / `blot` / `flyt`. Custom IDs are legal if every platform carries a complete parameter set.
6. **Municipal order.** There is no regulator actor. A simultaneous per-platform `policy_change` is taken as the intended shock.
7. **Balance sheet.** There is no platform cash ledger. Check 4 is read as “no completed-ride settlement has a negative `platform_contribution_minor` caused by mixing pre-cap driver payout with post-cap rider billing.”
8. **Trip A/B/C** are specific instances that must exist and be measurable, not merely statistical classes.

### 1. What is currently possible

The core research question is already answered by the shipped engine: **pricing and commission are bound onto quote/offer/order records at decision time and are not re-read from live platform policy at completion.** A mid-run municipal tariff shock can be expressed as timed `policy_change` interventions. In-flight and pre-accepted rides keep those frozen terms.

**World, population, two platforms, 24 hours.** `Scenario` + `compile_scenario` already accept `world.horizon_hours = 24`, `population.riders.count = 250`, `population.drivers.count = 60`, rotation or explicit shifts, and weekly or explicit trips ([scenario-definition.md](../../plans/architecture/scenario-definition.md); schema in `scenario.py` `SCENARIO`). A compile probe (seed 1, not run) produced 250 riders, 60 multihoming drivers on `rebu`/`blot`, 250 weekly trips, 60 rotation shifts, and two `policy_change` rows at `t = 12 h`. Preset leftovers (`flyt`, default segments) can be removed with `Scenario.remove`. Custom IDs `apex` / `zenith` also compile if `platforms` is replaced with complete `MARKETPLACE_DEFAULTS_V1` plus the shock tariffs.

**Pre-shock commercial terms.** Both platforms can be set to a surge-like $4.00/km and 25% take-rate by writing `platforms.<id>.policy.parameters.per_km_minor = 400` and `commission_fraction = 0.25` (minor units are cents: `World.minor_units_per_major = 100`, `marketplace_engine.py` `minor_units` / `to_minor_units`). There is no automatic surge controller (`MarketplacePolicy.controller` returns `Stop('fixed policy')`; [marketplace-policy.md](../../plans/architecture/marketplace-policy.md) lists automatic surge as an extension). For this scenario a static $4.00/km parameter is enough.

**The t = 12.00 shock.** `policy_change(id, at_hours=12, platform=..., version=..., parameters={...})` (`scenario.py` lines 600–603) compiles a new `PlatformPolicy` by merging omitted fields with the platform’s current definition (`compile_scenario` intervention block, lines 980–985). `main.Simulation._schedule_intervention` compiles that policy again and `PolicyRuntime.schedule_intervention` / `_intervene` (lines 605–639) **replaces** `self.platforms[platform_id]` at the scheduled second. Lookup is half-open in the sense `item['at'] > self.now` is skipped, so `at == now` applies. Same-time events run in scheduler insertion order (`event_engine.py` `(at_seconds, sequence)`). There is no market-wide intervention kind; Apex and Zenith each need their own row at `t = 12`.

**Contract immutability (the mechanism the scenario is probing).**

- Rider terms: `FareTerms` is documented as “Quoted rider terms, frozen at quote time and bound by the order” (`marketplace_engine.py` lines 139–153). `issue_quote` stores `Quote.fare`; `place_order` copies `quote.fare` onto `Order.fare` (lines 880–929).
- Driver terms: `PayoutTerms` is “Offered driver terms, frozen at offer time and bound by acceptance” (lines 156–169). `create_offer` stores `Offer.payout`; `_accept` copies `offer.payout` onto `Assignment.payout` (lines 1026–1029).
- Commission used at offer time is the **then-current** `driver_params.commission_fraction` applied to the **already-bound** `order.fare.gross_minor` (`MarketplacePolicy.dispatch`, lines 275–278). It is not recomputed later.
- Completion: `_drop_off` calls `_settle(order, "completed_ride", order.fare.rider_payment_minor, order.assignment.payout.driver_payout_minor)` (lines 1155–1168). `_settle` writes `platform_contribution_minor = rider_payment_minor - driver_payout_minor` (lines 1243–1251). No path reads `self.platforms[...].config.parameters` at drop-off.
- Policy docs match the code: “Existing quote and accepted offer terms remain committed” ([README.md](../../README.md)); “A queue delay, campaign ending, tariff update, or later segment change cannot … reprice an existing order” ([marketplace-policy.md](../../plans/architecture/marketplace-policy.md)).
- ETA `revise` cannot change payments (`revise_pickup_eta` only appends `eta_predictions`). Cancellation fees consult current policy at request time (`MarketplacePolicy.cancel`); that is a different settlement reason and is outside A/B/C completions.

**Boundary trips as phases, via configuration.** Dispatch, pickup, boarding, and drop-off times are endogenous (search delays, offer response, geometry, `World.speed_kmh`, `boarding_seconds`). They are not scenario fields. They *can* be aimed with explicit people, shifts, and trips, as in `scenarios/fixture_cross_platform_queue.py`:

- Trip A: request several minutes before `t = 12`, pickup distance that arrives and boards before `t = 12`, transport long enough to complete after `t = 12`.
- Trip B: assignment before `t = 12`, pickup travel still open at `t = 12`.
- Trip C: `trip(..., at_hours=12.01, ...)` so `begin_intent` (`main.py` `_on_trip_start`) is after the shock.

Default rider `cancellation_after_seconds = 600` (`RIDER_DEFAULTS_V1`) cancels a ~10-minute pickup. That is a **configuration** conflict with a clock-style Trip A, not a missing mechanism. Raising the trait (or shortening pickup) is enough. Default `acceptable_price_ratio = 1.2` against `reference_per_km_minor = 150` will also reject a $4.00/km quote unless purchase bias / reference / ratio are raised (`RiderPolicy.decide`, `behavior_policy.py` lines 210–237). Fixture-style `purchase_bias` / `acceptance_bias` / `taste_scale = 0` makes A/B/C acceptances deterministic.

**Observable checks from current logs, without new metrics.** `Simulation.run` writes `simulation.log` with initial/final checkpoints that include `quotes`, `orders` (fare, assignment payout, timeline), `offers` (`policy_version`), `services`, and `settlements` (`rider_payment_minor`, `driver_payout_minor`, `platform_contribution_minor`). Notifications include `order_completed` with `settlement_id` and, to the rider, `rider_payment_minor`. `metrics.py` only aggregates money; it does not identify A/B/C or implied $/km. The four DESCRIPTION checks are still computable offline from the checkpoint:

| Check | How to read it |
| --- | --- |
| Trip A settles on original $4/unit and 25% | `Order.fare.gross_minor / quote.distance_km` and `1 - assignment.payout.payout_minor / fare.gross_minor`; `timeline["completed"]` after `t = 12`; `quote.policy_version` still the pre-shock label |
| Trip B does not reprice at pickup | same frozen fields; `timeline["assigned"] < t12 <= timeline["arrived"]` |
| Requests at `t >= 12` quote ≤ $2/unit and take ≤ 10% | `Quote.at >= 43200` and `quote.policy_version` of the cap version; implied rate from `fare` + `distance_km` |
| No mixed-rate negative margin | every `completed_ride` settlement: contribution = rider − driver from the *same* bound contract |

Identifying A/B/C requires joining `inputs.sessions` (explicit trip `id`, rider, origin, destination, `at_seconds`) to intents by rider + time + coordinates. `begin_intent` does **not** copy the activity session id onto `TripIntent`.

**Throwaway verification (reported because it was run).** Two `/tmp` fixtures used `three-platform-day@1`, explicit riders/drivers, `policy_change` on `rebu` at `at_hours=12` from `{per_km_minor: 400, base_fare_minor: 0, commission_fraction: 0.25}` to `{per_km_minor: 200, base_fare_minor: 250, commission_fraction: 0.10}`, and saturating choice traits.

- First probe: a pre-shock 5 km order quoted `gross_minor = 2000` (exactly $4.00/km) and `payout_minor = 1500` (25%). After the shock, live policy was `cap-v1` / 200 / 0.10, but the bound order was unchanged. It canceled because pickup wait exceeded the default 600 s patience. A post-shock 5 km trip quoted `gross_minor = 1250` (`250 + 200 * 5`) and paid `1125` (10%), `policy_version = cap-v1`.
- Second probe (patience 1200 s, longer A transport): pre-shock trip A completed **after** `t = 12` with `gross 4900` on 12.25 km, take 0.25, contribution `1225`. Live policy was already `cap-v1`. A post-shock trip used cap terms. No settlement mixed a capped rider bill with a pre-cap driver payout.

That is the intended “no retroactive adjustment” outcome, and it is the engine default rather than a special regulatory mode.

**Limits of what “currently possible” includes.** Weekly trips sample uniform grid/continuous points on `world.map_km` (default 10×10 km; `_sample_point` in `scenario.py`). That is not an airport–downtown corridor. High corridor demand is only possible by listing explicit origins/destinations. Exact DESCRIPTION minutes are not inputs. Quote and offer can straddle `t = 12` (30 s default `quote_seconds`): a pre-shock quote ordered after the shock keeps the pre-shock rider fare, while a post-shock offer applies the new commission to that frozen gross. DESCRIPTION does not name that mixed case; A and B as specified have both quote and accept before `t = 12`. Negative contribution is *allowed* by the money contract when discounts/bonuses exceed commission ([marketplace-policy.md](../../plans/architecture/marketplace-policy.md)); this scenario does not use those, so check 4 should pass for completed A/B/C.

### 2. What is almost possible with a small, quick, independent adjustment

None of the following is required to answer the modeling question, but each is a localized gap if the review wants DESCRIPTION’s wording more literally.

**Carry the activity trip id onto the intent/order.** `main.Simulation._on_trip_start` calls `begin_intent(rider, origin, destination)` and drops `event.payload`’s session `id`. A single optional `label=` / `source_id=` on `TripIntent` (and copied to `Order`) would make Trip A/B/C first-class in the log. Independent of pricing. Verify by asserting `order.source_id == "trip-a"` in a fixture.

**Bind commission at the same instant as “dispatch.”** Today fare and take-rate can be from different policy versions if quote and offer straddle `t = 12`. Smallest fix aligned with DESCRIPTION’s “dispatched” rule: compute payout in `MarketplacePolicy.dispatch` from a commission stored on the quote/order at `place_order`, or refuse to dispatch a quote whose `policy_version` no longer matches. That is a few fields and one assignment, not a new subsystem. Verify with a quote issued at `t = 11.999` and an offer after `t = 12`.

**True ceiling, not a replacement tariff.** If the municipal order must *clamp* whatever a platform would have charged (`min(platform_rate, 2.00)`, `min(commission, 0.10)`) while leaving unconstrained intent in the platform’s own parameters, add optional `max_per_km_minor` / `max_commission_fraction` and apply them inside `quote` and `dispatch`. Small and independent. This scenario’s platforms already charge exactly the cap values, so replacement is observationally identical.

**A single multi-platform intervention.** `_intervene` is already per-row. A `platforms: ["apex", "zenith"]` list, or applying one compiled policy to every launched platform, would match “municipal order” without two copy-pasted `policy_change` items. Convenience only.

**Offline check helper.** A short reader over `simulation.log` that prints implied $/km, implied take-rate, A/B/C phase flags, and `min(platform_contribution_minor)` would make the four checks turnkey. That can live outside the engine (`metrics.py` is the natural place) and does not change runtime.

Do not treat default rider patience, reference fares, or Apex/Zenith labels as code work: they are scenario parameters (see §1).

### 3. What is not currently possible and what must be developed

If a category has no findings beyond the approximations already listed, that is stated.

**No regulator object and no market-level constraint layer.** Platforms change their own compiled policy. Nothing in `marketplace_engine.py` rejects a quote above a municipal cap. If the experiment needs a third-party rule that remains in force even when a platform’s `per_km_minor` stays at 400, that is a new engine check (or a mandatory wrap the policy cannot bypass). A `policy_change` that *sets* 200/0.10 is not that rule; it is a platform update. Approximation: replacement tariffs. Lost: platforms independently trying to exceed the cap, enforcement vs. compliance, and any audit of attempted illegal quotes.

**No endogenous surge to be capped.** “Surge-inflated $4.00 per unit” cannot be produced by a demand/supply multiplier that then hits a ceiling. `multiplier` is a static parameter. Building automatic surge, then clamping it, is new marketplace-policy work already labeled an extension in [marketplace-policy.md](../../plans/architecture/marketplace-policy.md). Approximation: constant $4.00/km. Lost: the cap interacting with a moving surge surface.

**No airport–downtown corridor generator.** `activity.trips.weekly` draws independent random points. There are no named places, OD weights, or corridor geometry. A reusable corridor (airport cluster → downtown cluster, high demand) needs a new trip generator or a large explicit list. The pricing question does not require it; the “high demand across an airport-to-downtown transit corridor” setup does, if taken as a spatial process rather than flavor text.

**Dispatch / pickup / drop-off times cannot be authored as facts.** `trip.at_hours` is intent-start time. Matching, boarding, and travel then follow physics and policy delays. You cannot write “dispatched at 11.45, picked up at 11.55, finishes at 12.20” as inputs and have the engine treat those as scheduled facts. Approximating them with geometry is §1; a scripted ride clock would be a different (and undesirable) engine.

**No platform balance-sheet / running cash account.** `_settle` appends an immutable residual. There is no cumulative Apex/Zenith book, no “transaction margin” account, and no invariant that contribution stay non-negative (negative contribution is legal). Check 4 can still be evaluated per settlement. A true balance-sheet report is out of scope here and belongs with later solvency work, not this scenario’s contract-immutability question.

**Unresolved DESCRIPTION contradictions are not implementation gaps.** The $2.00/unit cap vs $2.50 base, and decimal-hours vs `HH.MM`, must be decided by the scenario author. The engine cannot implement both readings at once.

### 4. Documentation quality and code readability

**What made the assessment easy.** The root [README.md](../../README.md) states the immutability rule in one sentence (“Existing quote and accepted offer terms remain committed”) and points at `policy_change` plus `scenarios/blot_discount_week.py`. [marketplace-policy.md](../../plans/architecture/marketplace-policy.md) gives the fare equation, freeze points (quote vs offer), half-open campaign windows, and the explicit statement that tariff updates do not reprice accepted orders. `FareTerms` / `PayoutTerms` / `_drop_off` / `_settle` are short, named, and match those docs. `Quote` and `Offer` store `policy_version`, so a log reader can see *which* compiled version bound the contract. `blot_discount_week.py` is the right commercial-intervention example (one platform, campaign then tariff). `fixture_cross_platform_queue.py` is the right explicit A/B/C construction example.

**What was missing or easy to misread.**

- There is no worked example of a policy version changing *while a ride is physically in progress*. A reader could still fear that `_drop_off` consults live policy; only reading `_settle`’s arguments closes that. A three-line note next to `_drop_off` or in marketplace-policy “Cancellation and policy changes” would have made the review faster.
- DESCRIPTION’s Apex/Zenith, “per unit”, and `t = 11.45` do not use repository vocabulary. A reviewer must translate without a glossary. Scenario-review [README.md](../README.md) does not warn about that.
- Quote-time fare vs offer-time commission is documented but easy to miss. The modeling challenge asks a binary (match-time vs completion-time); the implementation is *two* bind times. The docs should say that in one place.
- Replacing the `platforms` table (to rename Apex/Zenith) drops inherited `MarketplaceParameters` and fails compilation with a long missing-key list. `policy_change` merge *does* inherit. That difference is correct but surprising; `platform_policy()` / a “copy preset platform” helper would help.
- `metrics.py` documents aggregate settlements well and does not claim per-trip rate checks. Call that a documentation success: it does not over-promise. The gap is example analysis code for this class of check.
- No unit test suite (project policy). Confidence in immutability comes from reading `_drop_off` and running a fixture, not from a named regression. A five-assertion throwaway in `scenarios/` would be the highest-leverage readability/assurance improvement for this exact question.

**Readability of the execution path.** The path `policy_change` → `compile_scenario` merge → `Simulation._schedule_intervention` → `PolicyRuntime._intervene` → later `MarketplacePolicy.quote`/`dispatch` → `issue_quote`/`create_offer` → `_drop_off`/`_settle` is linear and does not hide a second pricing pass. Confidence in “not re-evaluated at completion” is high. Confidence that a *population-scale* weekly run will spontaneously contain A/B/C-shaped rides is low without explicit trips; that limit is also readable from `_realize_activity`.
