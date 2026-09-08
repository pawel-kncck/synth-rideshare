# Scenario 10 analysis — UNKNOWN

- **Model id:** `UNKNOWN` (reviewing agent model id was unavailable in this offline/web-inspection path)
- **Review date:** 2026-09-09
- **Repository revision:** `28e839eae51358d7646ae0a5f156508ba30b1c11` (`main` tip at inspection time)
- **Method:** Inspection only via public raw.githubusercontent.com / GitHub Contents & Commits APIs (no clone, no scenario execution). Conclusions below are based on reading source and architecture docs, not on a live run.
- **Other reports in folder:** none at inspection time (folder contained only `DESCRIPTION.md` and `README.md`; parent `scenario-reviews/README.md` states no analyses are included).

Scenario under review: [DESCRIPTION.md](DESCRIPTION.md) — three-way Corp / Burn / Coop price war with endogenous platform solvency, driver vehicle debt, and Coop dividends.

---

### 1. What is currently possible

**Multi-platform, multi-day market setup (population, horizon, three competitors).**  
The scenario compiler accepts arbitrary platform IDs under `platforms` (`scenario.py` `PLATFORM` / `SCENARIO` schema ~L481–529) and published presets already wire three launched apps (`rebu`, `blot`, `flyt`) via `_base_v1` / `ALL_APPS` (`scenario.py` ~L642–706). Counts, horizon, and activity are configurable with `with_changes` (e.g. `world.horizon_hours: 336`, `population.riders.count: 300`, `population.drivers.count: 60`). Existing examples stretch a week preset to 30 days with 1,000 riders / 50 drivers (`scenarios/flyt_growth_month.py`). Mapping Corp→`rebu`, Burn→`blot`, Coop→`flyt` (or replacing the platform table with `corp`/`burn`/`coop` IDs) is therefore expressible without new engine features. Currency is integer minor units with `minor_units_per_major: 100` in presets, so dollar amounts map cleanly to cents.

**Asymmetric take-rates (20% / 0% / 5%).**  
`commission_fraction` is a first-class marketplace parameter (`MARKETPLACE_DEFAULTS_V1` in `scenario.py` ~L635–641; `MarketplaceParameters` / offer math in `marketplace_policy.py` ~L68–99, ~L274–278). Driver base payout is `(1 - commission_fraction) * gross` (rounded half-up). Setting Corp `0.2`, Burn `0.0`, Coop `0.05` reproduces the take-rate part of the starting conditions. Offer terms freeze at acceptance (`PayoutTerms` in `marketplace_engine.py` ~L156–169).

**Rider fare subsidy / 50% Burn discount without reducing base driver payout.**  
Campaigns support `discount_fraction` (optionally capped) over a half-open `[start, end)` window (`Campaign` in `marketplace_policy.py` ~L19–58; builder `campaign()` in `scenario.py` ~L551–555). Quote path applies the best eligible discount to gross fare; rider payment is `gross - discount` (`FareTerms.rider_payment_minor`, `marketplace_engine.py` ~L140–153). Architecture and code both state that rider discounts are platform-funded and do **not** shrink base driver payout (`plans/architecture/marketplace-policy.md`, pricing identity; offer payout uses gross × (1 − commission)). A Burn campaign with `discount_fraction=0.5`, no `discount_cap_minor`, spanning the full horizon, plus `commission_fraction=0.0`, yields per completed ride:

- discounted rider fare = \(0.5G\)
- driver payout = \(G\) (no bonus)
- `platform_contribution_minor` = rider_payment − driver_payout = \(-0.5G\)

so the economic identity in the first observable check,  
\(\text{Driver Payout} - \text{Discounted Rider Fare} = -\text{platform_contribution}\),  
is already encoded in each `Settlement` (`marketplace_engine.py` `_settle` / `_drop_off` ~L1167–1251). Negative contribution is explicitly allowed and tested in docs (`marketplace-engine.md` money section).

**Per-trip money observability from the raw log (partial check #1).**  
`Simulation.run()` writes settlements into the final checkpoint (`README.md`; `Settlement` fields include `platform_id`, `rider_payment_minor`, `driver_payout_minor`, `platform_contribution_minor`, `reason`). Offline `metrics.py` aggregates whole-run money totals (`calculate_metrics` ~L343–379) and documents that contribution excludes operating costs/taxes. Filtering final-state settlements by `platform_id == burn` and sorting by `at` lets an analyst reconstruct a **synthetic** Burn cash path \(C_0 + \sum contribution\) and verify monotonic drain equal to payout − rider payment **as an accounting identity**. Limits: `metrics.py` does **not** emit per-platform money series or a cash-balance time series; the engine never stores a running corporate reserve; and this reconstruction does not feed back into dispatch.

**Rider preference for cheaper quotes (approximate “price above all else”).**  
`RiderPolicy` utility penalizes net rider payment via `price_sensitivity` and also ETA, loyalty, search cost, and taste (`behavior_policy.py` ~L210–237). Defaults mix price and ETA (`RIDER_DEFAULTS_V1`, `scenario.py` ~L618–622). Raising `price_sensitivity`, lowering `eta_sensitivity` / `loyalty` / `taste_scale`, and ensuring multihoming apps so Burn’s discounted quote is seen, makes Burn strongly preferred when its quote is live. This is **not** a hard lexicographic “cheapest only” rule; residual ETA/loyalty effects remain unless tuned aggressively. Notes.md states riders prefer cheaper fares in the synthetic baseline.

**Platform launch gating (one-way).**  
`Platform.launched` gates quotes/orders (`marketplace_engine.py` ~L180, ~L793–795, ~L894–926). Interventions support `launch` (unlaunched → launched) via `launch()` / `main.Simulation._schedule_intervention` (~L80–83). Useful for entrant timing elsewhere; **not** a shutdown/bankruptcy API (there is `launch_platform` but no unlaunch).

**Exogenous labor calendars and long horizons.**  
Shifts are scenario data (`rotation` / `explicit` / `none`). A 14-day war can schedule crews covering midnights; evolution checkpoints can fire every 24h (`evolution.checkpoint_hours`). These do **not** implement vehicle leases or debt-driven overtime—they only show that clock time, daily checkpoints, and multi-day populations are already supported.

**Assumption for this section:** “possible” means the commercial setup and per-ride subsidy accounting can be **configured and observed offline**, not that capital exhaustion, driver bankruptcy, or dividends endogenously reshape supply/demand.

---

### 2. What is almost possible with a small, quick, independent adjustment

**Per-platform settlement / contribution metrics (observability of Burn drain).**  
**Gap:** `calculate_metrics` sums money across all platforms (`metrics.py` ~L345–379); interval rows omit money. Check #1’s monotonic Burn cash path is reconstructible only with a custom log script.  
**Smallest fix:** In `metrics.py`, group `new_settlements` by `platform_id` (and optionally by completion time buckets) and emit `platform_settlements` / cumulative contribution. No engine change.  
**Why small:** Pure offline aggregation over records already in the log.  
**Verify:** Run any campaign scenario (e.g. `blot_discount_week.py`), confirm Burn/Blot contribution totals match filtered settlement sums.

**Platform cash ledger updated at settlement (tracking without full bankruptcy product).**  
**Gap:** `Platform` holds only `id`, `name`, `launched`, open participant sets (`marketplace_engine.py` ~L177–182)—no treasury. Scenario `PLATFORM` schema has no starting reserve (`scenario.py` ~L481–484). DESCRIPTION’s $50k / $15k / $2k reserves cannot be authored.  
**Smallest fix:** Add optional `starting_cash_minor` on platform config; store `cash_minor` on `Platform`; in `_settle`, add `platform_contribution_minor` to that platform’s cash; expose balances in snapshots/notifications.  
**Why small/independent:** Localized to schema binding + `_settle` + serialization; reuses existing contribution residual. Does **not** alone shut the app down.  
**Verify:** Fixture with one subsidized completion; assert cash decreased by payout − rider payment; snapshot restore preserves balance.

**Auto-shutdown when cash ≤ 0 (core Capital Exhaustion Rule, multihoming migration approximation).**  
**Gap:** Hitting $0 does nothing. Pending Burn work is not voided; Burn keeps quoting.  
**Smallest fix (builds on cash ledger):** After `_settle` (or on a tiny post-settlement hook), if `cash_minor <= 0` and still launched: set `launched=False`, cancel non-terminal own orders (engine already has `cancel_order`), cancel pending offers, notify. Existing launched checks then block new Burn quotes/orders. If the scenario starts everyone multihomed on Corp and Coop, riders’ search naturally moves to remaining apps—approximating “forced migration” without new preference machinery.  
**Why relatively small:** Reuses `launched`, `cancel_order`, and rider multi-app search.  
**What it still loses vs DESCRIPTION:** No scripted “migrate only to Corp or Coop” preference rewrite; in-flight boarded trips may still complete under frozen terms (docs freeze accepted payments—graceful failure of *requests* vs mid-transport abort needs an explicit policy choice); shutdown is instantaneous at the settling event, not necessarily aligned to an “exact hour” boundary unless cash is also checked on a hourly tick.  
**Verify:** Tiny treasury + deep discount; assert last Burn completion leaves cash ≤ 0, no later Burn orders/`platform_completions`, pending Burn offers canceled.

**Optional `shutdown` / `unlaunch` intervention for scripted (non-endogenous) collapse.**  
**Gap:** Interventions are only `launch` | `policy` | `preference` (`scenario.py` `INTERVENTION` ~L503–509).  
**Smallest fix:** Add a `shutdown` intervention calling the same unlaunch+cancel path, for experiments that inject Burn failure at a chosen time. Independent of cash math; does not satisfy endogenous exhaustion by itself.

**Not placed here:** daily $35 leases, −$100 driver bankruptcy/repossession, debt-driven longer hours, and Coop weekly dividends—these need new participant financial state and scheduled non-trip transfers (section 3).

---

### 3. What is not currently possible and what must be developed

**Endogenous corporate solvency feedback into marketplace supply/demand (main modeling challenge).**  
Even with section-2 cash + shutdown, the DESCRIPTION’s research question needs Burn’s collapse to **change** subsequent matching, ETAs, and competitor volumes over 14 days, and to interact with driver exits. Today contribution is an accounting residual only (`Settlement.platform_contribution_minor`); README/`marketplace-policy.md` state contribution is not profit and excludes operating costs. Campaigns have **no** budget / capital stop (`marketplace-policy.md`: aggregate budgets are extensions; a budget must not claw back promised settlement). Subsidies can run for the full window regardless of treasury. **Develop:** cash-constrained campaigns or solvency-triggered policy/shutdown wired into the runtime so capital exhaustion is an in-sim event that observers and later decisions see (notifications, metrics, abandoned Burn intents).

**Driver vehicle debt: $35 midnight lease, cumulative cash, −$100 bankruptcy, repossession, permanent removal.**  
**Missing state:** `Driver` has apps, car, shift, commitments—no ledger (`marketplace_engine.py` ~L206–214). No scheduled personal charges; evolution checkpoints update adoption/learning only (`behavior_policy.py` `EvolutionPolicy.checkpoint` ~L314+; `policy_runtime.py` `_checkpoint`).  
**Missing removal:** No API to permanently deactivate a driver from all platform registries / remove the car mid-run. `end_shift` only drains the current shift (`marketplace_engine.py` ~L754–778). Population and shift lists are fixed for the run (`behavior-policy.md`: exogenous trip/shift schedules never change).  
**Develop:** driver cash accounts; midnight (or daily reconciliation) lease postings; bankruptcy transition that closes apps, cancels commitments, frees/removes the car, and excludes the driver from future `DriverPresence` / shift starts; notifications + metrics for deactivation time and reason. Observable check #3 depends on this.

**Debt-driven labor supply (“cannot afford to log off; work longer hours”).**  
Driver acceptance uses payout/ETA logistic scores (`DriverPolicy.respond` ~L281–295); shift length and start times come from scenario generators (`main.py` exogenous sessions). There is no link from cumulative earnings or looming lease to overtime, delayed `end_shift`, or extra shifts.  
**Develop:** endogenous shift extension / recall policy reading driver ledger and daily net target (at least $35), with clear conflict rules against the fixed `conflicts: fail` activity policy. Without this, fixed debt only removes drivers; it does not generate the intended intensive-margin supply response under falling rates.

**Coop weekly dividend (excess cash above $2,000 to drivers with ≥20 Coop completions).**  
No dividend, surplus distribution, or non-trip driver credit exists. Settlements are only `completed_ride` or `cancellation_fee` (`Settlement.reason`). Platform-local completed-ride counts exist for campaign/`new_user` rules, which could support eligibility, but there is no transfer that debits Coop cash and credits many drivers while leaving historical trip fares untouched.  
**Develop:** scheduled weekly Coop finance event; eligibility query; ledger postings (platform cash ↓, driver cash ↑) distinct from order settlements; log fields so check #4 can prove trip pricing records unchanged. Approximation via a mid-run `bonus_minor` campaign would **alter** offer/trip economics and fail the “without altering trip-level pricing records” requirement.

**Observable checks #2–#4 as specified.**  
- **#2 (exact hour Burn ≤ $0 ⇒ terminate services, fail active requests, no further dispatches):** requires cash + shutdown (+ definition of “exact hour” vs event time) and graceful cancel semantics—not present.  
- **#3 (drivers < −$100 at daily reconciliation permanently deactivated):** requires driver ledger + removal—not present.  
- **#4 (Coop dividend moves cash, not trip prices):** requires dividend subsystem—not present.  
Check #1 is only partially satisfiable today as an offline identity on settlements, not as verification of a live Burn reserve.

**Forced migration after Burn shutdown for non-multihomers.**  
If some agents lack Corp/Coop apps, shutdown alone strands them until adoption/evolution installs alternatives. DESCRIPTION’s “forced to migrate” implies immediate usable access. **Develop:** shutdown handler that installs/activates remaining apps and/or `preference_change`-like rewrites, or require full multihoming as a documented scenario assumption (flag ambiguity).

**Ambiguities / assumptions (unresolved scenario requirements vs engine limits).**  
1. “Full market rate” for Burn drivers: interpreted as gross fare at 0% commission (no extra bonus).  
2. Cash decrease formula vs contribution: aligned when bonus = 0; bonuses would widen the drain.  
3. “Exact hour” of bankruptcy vs continuous event time.  
4. Whether boarded Burn trips abort at insolvency or finish on frozen terms.  
5. Coop “operating reserve $2,000” vs starting cash $2,000: treated as the same threshold for dividends.  
6. Corp/Burn/Coop names vs preset `rebu`/`blot`/`flyt`: cosmetic if parameters match.  
7. “Riders prioritize price above all else”: soft utility vs hard min-price rule.

---

### 4. Documentation quality and code readability

**What helped.**  
Root `README.md` clearly separates scenario compilation, execution, logging, and offline metrics, and points to architecture docs. `plans/architecture/marketplace-policy.md` is the strongest guide for take-rates, campaigns, frozen commitments, negative contribution, and explicit non-goals (campaign budgets, operating costs). `marketplace-engine.md` documents settlement conservation and scoped notifications. Preset builders and `scenarios/blot_discount_week.py` / `flyt_growth_month.py` show how to author asymmetric commissions and discounts. Typed dataclasses (`FareTerms`, `PayoutTerms`, `Settlement`, `Campaign`) make the money path readable end-to-end from quote → offer → `_drop_off` → `_settle`.

**What hindered or could mislead.**  
- No index of “financial state the sim does **not** keep” (platform treasury, driver cash, leases, dividends, bankruptcy). A reader could infer from “platform_contribution” and campaign discounts that solvency is modeled; docs correctly say contribution ≠ profit but bury the implication for capital exhaustion.  
- `scenario-reviews/` DESCRIPTION texts read like requirements (as intended) but sit beside a README that says phase-3 marketplace is “complete,” which is easy to over-read as covering these difficult scenarios.  
- Money metrics are global, not per-platform; README’s metrics section does not mention reconstructing firm-level burn from settlements.  
- Platform IDs in prose (Corp/Burn/Coop) never appear in code; mapping to `rebu`/`blot`/`flyt` is left implicit.  
- Behavior docs emphasize fixed exogenous shifts—crucial for rejecting debt-driven labor supply—but that limitation is easy to miss when scanning only marketplace-policy.

**Actionable suggestions.**  
1. Add a short “Non-goals / absent ledgers” subsection to `marketplace-engine.md` or README: no platform cash, no driver cash, no solvency shutdown, no dividends.  
2. Document the settlement identity `contribution = rider_payment - driver_payout` with a subsidized 0% commission example matching Scenario 10’s first check.  
3. Extend `metrics.py` docs/CLI for per-platform contribution totals.  
4. In `scenario-reviews/README.md`, note that reviews evaluate gaps against the current engine, not claimed features.  
5. Keep money path comments near `_settle` pointing to frozen fare/payout terms vs any future treasury hook.

**Confidence.** High that corporate cash, driver debt, dividends, and solvency-triggered shutdown are absent (no symbols/fields/APIs). High that take-rates, campaigns, and settlement residuals support the commercial setup and offline subsidy accounting. Medium on how small an auto-shutdown patch would stay once cancel-during-service and migration edge cases are specified—those product rules are underspecified in DESCRIPTION.md.
