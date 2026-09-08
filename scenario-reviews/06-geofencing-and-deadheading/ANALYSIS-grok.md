# Scenario 6 analysis: Asymmetric Geo-Fencing, Deadheading, and Suburban Supply Deserts

- **Model:** grok (Grok Bot / Review 06 Geofence; exact backend model id unavailable — using filesystem-safe id `grok`)
- **Review date:** 2026-09-08 (UTC) / 2026-09-09 00:20 Europe/Warsaw
- **Repository revision:** `28e839eae51358d7646ae0a5f156508ba30b1c11` (`main`; commit message “Add monthly market-share scenario and LLM review cases”, authored 2026-09-08T22:12:27Z)
- **Method:** inspection via GitHub API (`get_file_contents`) and raw file reads of `scenario.py`, `main.py`, `marketplace_engine.py`, `marketplace_policy.py`, `behavior_policy.py`, `policy_runtime.py`, `policy_contracts.py`, `metrics.py`, and `plans/architecture/{scenario-definition,behavior-policy,marketplace-policy,marketplace-engine}.md`. No local clone; no full scenario run. One small offline import of downloaded modules verified that `MarketplacePolicy.quote` still returns a `QuoteProposal` (fare > 0) for an origin/destination outside a notional Core box when candidates are empty.

Treats [DESCRIPTION.md](DESCRIPTION.md) as requirements, not as implemented features.

---

### 1. What is currently possible

**Two named platforms with independent tariffs and 20% take-rate.**  
`scenario.py` `PLATFORM` / `platforms` table (`SCENARIO` schema ~L510–530) supports arbitrary platform IDs (not only preset `rebu`/`blot`/`flyt`). Per-platform `MarketplaceParameters` include `per_km_minor`, `base_fare_minor`, `commission_fraction` (`marketplace_policy.py` `MarketplaceParameters` ~L62–106). Setting `base_fare_minor=0`, `per_km_minor=120` ($1.20/km with `minor_units_per_major=100`), `commission_fraction=0.2` compiles cleanly. Driver payout is `(1 - commission_fraction) * gross` at dispatch (`MarketplacePolicy.dispatch` ~L275); there is no empty-mile or repositioning payout path in `_settle` (`marketplace_engine.py` ~L1243–1251). So “pay loaded miles only, 20% take, no empty compensation” is configurable today.

**Multihoming drivers and dual app access.**  
Population segments / people declare `apps`, `accounts`, `registrations`, `preferred_app` (`scenario.py` `segment_fields` / `segment` ~L449–574; `PersonProfile` in `behavior_policy.py` ~L114+). Drivers with both platforms installed, accounted, and car-registered can open both apps (`marketplace_engine.py` `open_app` / `_driver_accepting` ~L789–840). Offers from multiple platforms can coexist (README; engine commitment cap of two). A single driver segment with `apps=["urban-only","city-wide"]` (or similar IDs) matches the multihoming requirement.

**24h horizon, population sizes, Core start locations.**  
`world.horizon_hours` and `population.riders|drivers.count` are first-class (`scenario.py` ~L515–525, presets ~L666+). Explicit shifts can place all 40 drivers at Core coordinates via `activity.shifts.generator="explicit"` and `location` (`SHIFTS` ~L488–493; `_realize_activity` ~L1361–1365). `start_shift` teleports the car to that location (`marketplace_engine.py` ~L741–751).

**CityWide accepting any in-map trip (as absence of geographic filter).**  
Neither `World` nor `issue_quote` / `place_order` / `create_offer` enforce a service polygon. `World` only holds speed, boarding, and currency (`marketplace_engine.py` ~L87–106). Coordinates are unconstrained beyond finite `(x,y)` km. So an unrestricted platform is the default behavior: any origin/destination that a rider intents will be quoted and may be dispatched.

**Empty (pickup) vs loaded (transport) distance is physically recorded.**  
Each completed service stores ordered `Leg`s with `kind` in `{"pickup","boarding","transport"}` (`marketplace_engine.py` `Leg` / `_begin_leg` / `_arrive` / `_board` / `_drop_off` ~L339–1162). Final checkpoint / log retains `services[*].legs` with origins, destinations, and times. Offline, empty km ≈ sum of `pickup` leg lengths; loaded km ≈ sum of `transport` leg lengths, per driver via `service.driver_id`. Settlements occur only on completed rides (and optional cancellation fees)—pickup travel does not create fares. So observable check 3 is **data-available** even though stock `metrics.py` does not yet emit the split (see §2).

**Utilization and online/idle time (partial proxy for “availability consumed”).**  
`metrics.py` clips shift online time vs physical service time (pickup + boarding + transport) and reports `utilization_pct` / `idle_driver_hours` (~L26–173, ~L287+). Dispatched empty pickup already counts as active time. Autonomous unpaid deadhead does **not** exist (see §3), so check 4 cannot be evaluated for true deadhead today—only for pickup legs tied to orders.

**Passive suburban waiting on CityWide.**  
After a CityWide drop-off in the Suburbs, the car becomes `Motion.idle` at the destination (`_drop_off` ~L1155–1162). The driver remains on shift and accepting while apps stay open. They can receive further CityWide offers from that position via nearest / pickup-ETA matching (`MarketplacePolicy.candidates` ~L227–241). That partially supports the “wait for organic Suburban CityWide request” branch of the behavioral choice—**without** the competing unpaid return-to-Core branch.

**Learning / opportunity memory exists, but not spatial.**  
`EvolutionPolicy.checkpoint` maintains platform-level `service_scores` and `estimated_wait_seconds` by idle/busy phase (`behavior_policy.py` ~L314–345; architecture `behavior-policy.md` ~L341–359). Useful later for zone-aware expectations, but current estimators are **platform × phase**, not zone × earnings-per-km.

**Limits / assumptions for §1.**  
“Currently possible” here means: population, dual platforms, tariffs, multihoming, 24h, Core shift starts, unrestricted CityWide semantics, and reconstructible pickup vs transport distances from logs. It does **not** mean the geo-fence, location-gated UrbanOnly dispatch, OD mixes by rider segment, or autonomous deadheading are expressible.

---

### 2. What is almost possible with a small, quick, independent adjustment

**A. Quote-time service-area rejection (observable check 1).**  
**Gap:** `PolicyRuntime.quote` requires `QuoteProposal` and always calls `engine.issue_quote` (`policy_runtime.py` ~L206–215). Built-in `MarketplacePolicy.quote` always returns a priced `QuoteProposal`, even with no candidates (`eta_seconds=None`) and with no OD bounds check (~L243–256). Verified offline: origin/destination `(10,10)→(12,12)` with empty candidates still yields `QuoteProposal` with `gross_minor > 0`. Rider policy only treats `eta_seconds is None` as “no supply,” not as platform refusal (`behavior_policy.py` ~L212).  
**Smallest fix:** (1) allow `quote` to return `Stop` (or a tiny `RejectQuote` type) and teach `PolicyRuntime.quote` to record a rejection observation without issuing a quote; (2) add optional `service_min` / `service_max` (or axis-aligned box) on `MarketplaceParameters` and reject when origin **or** destination falls outside. Touches `marketplace_policy.py`, `policy_runtime.py`, possibly `policy_contracts.py` / scenario schema binding—no new subsystem.  
**Verify:** scripted intents with OOD pickup/dropoff against UrbanOnly → 100% no quote / rejection log; Core→Core still quotes.

**B. Location-gated UrbanOnly dispatch (observable check 2).**  
**Gap:** `candidates` includes every locally open, accepting driver with a free local slot; it never filters on whether `driver.position` lies inside a platform polygon (`marketplace_policy.py` ~L227–241). `PlatformView.drivers` exposes absolute positions to all open platforms (~L436–455). Suburban drivers therefore remain UrbanOnly-eligible for Core pickups.  
**Smallest fix:** in `candidates`, skip drivers whose current position is outside the same service box used for OD rejection. Independent of deadheading; pairs naturally with (A).  
**Verify:** place a driver at `(10,10)`, create a Core UrbanOnly order → no offer to that driver until a (future) reposition re-enters `(0,0)–(5,5)`.

**C. Stock metrics for empty vs loaded km per driver (observable check 3).**  
**Gap:** `calculate_metrics` only sums **transport** legs into aggregate `completed_distance_km` (`metrics.py` ~L370–371). Pickup legs are ignored in that total; there is no per-driver distance breakdown.  
**Smallest fix:** offline aggregation over `final.engine` / tables: per `driver_id`, sum `pickup` vs `transport` leg lengths (and optionally ratio earnings / total km using settlements). Fits existing “metrics own all post-run calculations” design (README). No engine change.  
**Verify:** compare metric output to manual sum of legs in a short fixture run.

**D. Rider segment OD boxes and destination mixes (Core 80/20, Suburb 50/50).**  
**Gap:** `weekly` trip generation samples origin and destination independently via `_sample_point` over the full `world.map_km` (`scenario.py` ~L1336–1379). Segments control apps/traits, not home regions or OD mix. Explicit trips can hard-code coordinates but do not scale cleanly to 200 riders × probabilistic mixes.  
**Smallest fix:** extend the trip `Choice` generator (e.g. `regional` / per-segment fields: origin box, destination-box weights) inside `_realize_activity`, or generate explicit items in a scenario script from a seeded helper that is not yet part of the compiler schema. Localized to `scenario.py` (+ schema docs).  
**Verify:** compile/prepare and assert empirical OD frequencies and that Core riders’ origins lie in `(0,0)–(5,5)`.

**E. Map size 15×15.**  
`world.map_km` is already configurable (default `[10,10]` in presets ~L669). Setting `[15,15]` needs no code—only scenario overrides. (Note: `map_km` bounds sampling only; the engine does not clamp live coordinates to the map.)

These items are independent of autonomous deadheading. (A)+(B)+(D) together make the **geo-fenced market setup** runnable; they do not implement the §3 behavioral challenge.

---

### 3. What is not currently possible and what must be developed

**Autonomous unpaid deadheading / repositioning without an active dispatch (main modeling challenge; observable check 4).**  
**Missing behavior:** After suburban drop-off, a driver cannot choose to travel back to the Core unpaid while remaining “on shift” in a way that (i) updates physical position over time, (ii) consumes availability (no ghost fares), and (iii) is driven by historical earning expectations by spatial zone.  
**Evidence:** The only `Motion` starts are shift teleport (`start_shift`) and service legs (`_begin_leg` for pickup/boarding/transport). Grep across engine/behavior/runtime/main shows no `reposition` / `deadhead` / relocate command. Driver policy hooks are only `expand`, `respond`, `progress` (`behavior_policy.py` `DriverPolicy` declaration ~L254–256)—no idle reposition decision. Ending and restarting a shift to teleport would go offline, clear apps, and skip transit time—invalid as deadhead.  
**What to develop:**

1. **Engine:** a first-class unpaid relocation trajectory (e.g. `begin_reposition(driver_id, destination)` → `Motion` + scheduled arrival) that does not create orders/settlements; optionally mark the driver non-accepting or accepting-but-moving per policy; log leg kind distinct from `pickup`/`transport` so metrics do not confuse dispatched empty with deadhead.
2. **Behavior policy:** idle decision comparing expected net $/total-km (or wait) for “stay & take CityWide suburb demand” vs “deadhead to Core for UrbanOnly access,” using private memory.
3. **Spatial memory:** zone definitions (Core vs Suburbs) and estimators of earnings / offer rates **by zone** (today’s evolution memory is platform-level opportunity rates, not spatial—`behavior_policy.py` ~L314–345).
4. **Metrics / checks:** deadhead km and time separate from pickup empty miles; assert no settlements during reposition; net earnings / total km including empty.

**Approximation that loses the requirement:** Leave drivers idle in suburbs and let CityWide eventually pick them up. That models a supply desert and passive waiting, but loses the competitive unpaid return, UrbanOnly access recovery, and earnings-per-total-km tradeoff that define the research question.

**True geo-fence as a platform product feature (beyond the §2 patch).**  
Docs already list related non-features: “Automatic surge, broadcast dispatch, **road-network routing**, campaign budgets…” (`marketplace-policy.md`). There is no `service_area` on `PLATFORM` (`scenario.py` ~L480–484). A minimal axis-aligned box (§2) may suffice for this scenario; richer polygons, map topology, or engine-enforced legality would be larger work. Uncertainty: whether product intent is “policy soft-reject” only or engine hard-reject of OOD orders—scenario text says “rejected at query time,” which fits policy/runtime, not necessarily engine validation.

**Driver earnings-per-total-km tracking as a first-class behavioral signal.**  
Settlements and legs exist, but drivers do not maintain running net $ / (empty+loaded) km in policy memory for decisions. Evolution rewards are normalized platform scores, not spatial $/km. Building that loop is part of the deadhead subsystem above.

**Ambiguity / assumptions.**  
- DESCRIPTION’s Core `(0,0)–(5,5)` vs Suburban `(6,6)–(15,15)` leaves a strip / non-cover of the square map; analysis assumes exclusive boxes as stated and does not invent fill-in zones.  
- “Retention” is not an explicit simulator outcome (no multi-day quitting model beyond shift schedule and preference learning); earnings and utilization are the measurable proxies.  
- Custom registered marketplace policies (`policy_runtime.register_policy`) cannot currently refuse quotes: runtime still demands `QuoteProposal` (§2A).

---

### 4. Documentation quality and code readability

**What helped.**  
Root `README.md` clearly maps files to responsibilities and points to architecture docs. `plans/architecture/marketplace-policy.md` is precise on tariffs, matching, ETA estimators, and what is **not** shipped (surge, road network, etc.). `marketplace-engine.md` command table and view contracts make it easy to see that platforms observe positions but do not get geographic eligibility knobs. `scenario-definition.md` documents exogenous trips/shifts and segment access. Code is generally readable: frozen policy configs, typed proposals, and half-open expiry rules are consistent.

**Gaps / friction for this scenario.**  
- No documentation of service areas, geo-fencing, deadheading, or empty-mile compensation—search for those terms in repo docs/code finds nothing material; reviewers must infer absence.  
- `world.map_km` sounds like a hard world bound but only affects trip/shift sampling (`_sample_point`); live `begin_intent` does not clamp—easy to misread.  
- Quote semantics: `eta_seconds=None` means “no local supply,” not “platform refuses this OD”; that distinction is critical for check 1 and is easy to miss until `PolicyRuntime.quote` and `Quote.drivers_available` are traced.  
- `metrics.py` documents utilization clipping well, but does not mention reconstructing empty vs loaded distance from `legs`, despite the data existing—hurts check 3 discoverability.  
- Behavior docs describe opportunity exposure by app/phase thoroughly, which can be mistaken for spatial earning expectations; they are not.

**Readability / confidence.**  
Execution paths for quote → order → offer → service legs are clear and citeable; confidence is high that geo-fence and deadhead are absent rather than hidden. Confidence is slightly lower on the intended long-term extension point (new `Leg.kind` vs separate reposition record) because no stub exists—flagged as design choice, not observed code.

**Actionable suggestions.**  
1. Document “geographic eligibility” explicitly under marketplace policy extensions (service box parameters; quote may refuse).  
2. Clarify in scenario-definition that `map_km` is a sampling extent, not an engine fence.  
3. Add a metrics note (or helper) for per-driver pickup vs transport km from service legs.  
4. In behavior-policy.md, state that idle repositioning / zone-level $/km learning are out of scope for the shipped driver policy.
