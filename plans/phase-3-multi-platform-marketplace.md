# Phase 3: a three-platform marketplace (Rebu, Blot, Flyt)

## Objective and dependency

Turn the single-operator market into a marketplace of three competing ride-hailing
platforms, **Rebu**, **Blot**, and **Flyt**. Riders and drivers are free to use any
platform, but each person only has some of the apps installed, opens a preferred
one first, and switches to another only when the preferred one disappoints them.
Each platform sets its own tariff and can run time-boxed rider discounts and driver
bonuses. The installed base, preferences, and market shares must be able to change
over the course of a run, both by scenario decree and in response to what people
experience.

This is a standalone implementation handoff for a new session. It depends on the
autonomous market from [Phase 2](phase-2-autonomous-simulation.md) as it exists in
`main.py`, `behavior.py`, `demand.py`, `metrics.py`, and `reporting.py` today. Read
those files, the README, and the tests before editing. Work through the milestones in
order; each one leaves the simulator runnable and the existing tests green.

## Guiding decisions

These resolve the questions the request leaves open. Change them only with a reason
recorded in the completion notes.

1. **Single-platform runs stay byte-for-byte identical.** `Simulation(...)` without a
   `platforms` argument builds one platform named `Rebu` from the existing `base_fare`
   and `price_per_km`, installs it on everyone, and makes no new random draws. Every
   existing scenario and test keeps its seed-0 numbers (the 100k week must still
   report 94,773 completions and 72.12% utilization).
2. **The simulation's `base_fare` / `price_per_km` become the market's reference
   tariff.** Platform tariffs default to it. A rider judges "price too high" against the
   reference tariff for their trip distance, not against a fixed number, so long trips
   do not trigger switching by themselves.
3. **A rider opens apps sequentially and orders the best quote they have seen.** The
   preferred app is opened first; each further app is opened only when the current
   quote is unacceptable (no drivers, price above the rider's threshold, or ETA above
   the rider's threshold). Once the rider stops opening apps, they choose among all
   quotes seen using the existing logistic order score, then draw the order decision on
   that quote. Quotes are not refreshed while the rider thinks; search never reserves.
4. **Drivers multi-home while idle and return to their preferred app after each
   ride.** A driver goes online on the preferred app only. After `idle_patience_seconds`
   without an accepted order they also open the next installed app, and so on. Accepting
   a ride makes them busy on every platform. Completing it returns them to the preferred
   app alone and restarts the idle timer. (A per-driver "keep apps open" flag can be
   added later; it is not needed for the first version.)
5. **One pending offer per driver, across all platforms.** A driver considering a
   Blot offer is ineligible on Rebu and Flyt until it resolves. This keeps the Phase 1
   reservation invariant unchanged.
6. **Incentives are money the person sees.** A rider discount lowers the quoted price
   and feeds the rider's order decision; a driver bonus is added to the fare in the
   driver's acceptance decision and in earnings. No commission is modelled yet; the
   plan leaves room for a per-platform `commission_fraction` later.
7. **Market change comes through two doors.** Scenarios schedule installs, uninstalls,
   and preference changes explicitly (the same "the scenario schedules, the clock
   plays" contract as sessions). Inside the run, experience-driven adoption and
   preference re-ranking happen only at moments a person is already active (session
   end, shift end, idle timer), so no population scan is ever needed.
8. **Randomness stays partitioned.** ID generation, scenario sampling, and order and
   acceptance draws keep their current RNGs. Adoption and preference draws use a new
   private `random.Random(seed)`; population assignment in scenarios uses its own.
   Adding a draw in one stream must not move any other stream.

## Domain model

### `platforms.py` (new)

```text
Incentive(start_seconds, end_seconds, fraction=0.0, amount=0.0)
    Half-open window in simulated seconds after the run's origin, like session
    schedules. fraction is a share of the list price; amount is a flat sum.
    Overlapping windows for one platform use the greatest resulting value.

Platform(name, base_fare, price_per_km,
         rider_discounts=(), driver_bonuses=(),
         rider_adoption=(), driver_adoption=())
    list_price(distance_km)                 -> base_fare + distance_km * price_per_km
    rider_price(distance_km, at_seconds)    -> max(0, list * (1 - fraction) - amount)
    driver_payout(price, at_seconds)        -> price + price * fraction + amount
    adoption_probability(role, at_seconds)  -> chance an uninstalled person installs
                                               after a bad experience (see Milestone 5)

RiderProfile(installed: tuple[str], switch_price_ratio, switch_eta_seconds,
             experience: dict[str, float])
DriverProfile(installed: tuple[str], idle_patience_seconds,
              earnings_per_hour: dict[str, float])
```

`installed` is ordered; the first entry is the preferred app. Names must be unique,
non-empty strings; fares nonnegative; ratios finite and at least 1; seconds finite and
positive; incentive fractions in `[0, 1]`; windows with `start < end`. Validate in
`__post_init__` with `behavior.finite_number`, matching `demand.PeakPeriod`.

### Changes in `main.py`

- `Simulation.__init__` gains `platforms=None`, `rider_profiles=None`,
  `driver_profiles=None`, `app_switch_delay_seconds=20`. `self.platforms` is an
  ordered dict by name. Missing profiles default to "all platforms installed, first one
  preferred" with the trait defaults below, so a scenario can pass only `platforms`.
- `SearchResult` gains `platform`, `list_price`, `discount` (price stays the amount the
  rider pays). `SearchRecord` gains `platform` and `session_id`.
- `RiderSession` gains `quotes: dict[str, SearchResult]` in opening order,
  `platforms_opened: list`, `chosen_platform`. `search_result` remains the quote the
  rider is currently looking at, so existing code and tests keep working.
- `DriverSession` gains `open_platforms: list`, `idle_since`, `idle_epoch` (an
  integer bumped each time the driver becomes idle), and `next_app_deadline`.
- `Order` gains `platform` and `driver_payout`. `Offer` gains `platform` and
  `payout` (fare plus bonus at offer time).
- `_search(rider_session, platform)` quotes on one platform: price from
  `platform.rider_price`, ETA from the nearest driver whose session has that platform
  open and is `"waiting for order"`.
- `_driver_is_eligible(driver_session, platform)` adds the open-platform check.
  `_dispatch_order` uses it with `order.platform`.
- `rider_order_chance(quote)` is unchanged in shape. `driver_acceptance_chance(payout,
  eta_seconds)` receives the payout instead of the fare.
- New market events: `app_opened` (rider, platform, quote, acceptable flag),
  `platform_chosen`, `driver_app_opened`, `app_installed`, `app_uninstalled`,
  `preference_changed`. Existing events gain a `platform` field where one applies.

## Milestone 0 — freeze the baseline

1. Add `tests/test_baseline.py` that runs `scenarios/scenario_100_riders.build_scenario`
   equivalents at seed 0 without playback and asserts the exact summary counts observed
   today (sessions, orders, completions, cancellations, rejected/expired offers). Record
   the numbers from a run on the unmodified code in the test.
2. Keep this test through the whole phase. It is the proof of guiding decision 1.

## Milestone 1 — platforms, tariffs, and a fixed installed base

1. Create `platforms.py` with `Incentive`, `Platform`, `RiderProfile`, `DriverProfile`,
   and validation. No dynamics yet: incentive tuples may be empty.
2. Thread `platform` through search, session, order, offer, dispatch, and logs as
   described above. When `platforms` is omitted, build the single default platform and
   profiles so that behaviour is identical to today.
3. Behaviour in this milestone: a rider opens only the preferred app; a driver is
   online only on the preferred app. This already yields a working three-platform
   market with fixed shares and separate tariffs.
4. Write `config.json` schema version 3: a `platforms` section (tariff and incentive
   windows) and a `population` section with counts by installed portfolio and by
   preferred app for riders and drivers.
5. Tests: two platforms with different tariffs quote different prices for the same
   trip; a driver online on Blot is invisible to a Rebu search and never receives a
   Rebu offer; single-platform baseline unchanged; validation errors name the field.

## Milestone 2 — riders switch apps

1. Replace the single decision step with a small loop. `_start_rider_session` opens
   the preferred app (search, log, `app_opened` event) and schedules
   `_decide_on_quote` after `order_delay_seconds`, as now.
2. In `_decide_on_quote`, the current quote is **unacceptable** when it has no
   drivers, when `price > switch_price_ratio * reference_price_for(distance)`, or when
   `eta_seconds > switch_eta_seconds`. If it is unacceptable and an installed app has not
   been opened yet, schedule `_open_next_app` after `app_switch_delay_seconds`; that
   searches on the next platform and schedules `_decide_on_quote` again after
   `order_delay_seconds`. Guard every callback with the session state and
   `len(platforms_opened)` so stale callbacks return quietly.
3. When the quote is acceptable, or no apps remain, pick the quote with the highest
   `rider_order_chance` (ties by opening order), record `platform_chosen`, and draw the
   order decision on it. A rider with no quote showing drivers leaves with
   `"no drivers available"`; a declined quote keeps `"quote declined"`.
4. `_create_order` uses the chosen quote's platform and price. Dispatch and offers are
   restricted to that platform.
5. Trait defaults (synthetic assumptions, documented in the README): `switch_price_ratio`
   1.3, `switch_eta_seconds` 600. Scenarios draw per-rider values from bounded ranges
   (Milestone 6).
6. Tests with forced probabilities: rider with one app never switches; expensive
   preferred quote opens the second app and the cheaper one wins; a preferred app
   without drivers falls through to one that has them; the total think time equals
   `n * order_delay + (n - 1) * switch_delay`; a rider whose thresholds are never
   crossed orders on the preferred app even when another app would be cheaper.

## Milestone 3 — drivers multi-home when idle

1. `_start_driver_session` sets `open_platforms = [preferred]`, `idle_since = now`,
   bumps `idle_epoch`, and, if the driver has more than one app, schedules
   `_open_next_app_for_driver(session, epoch)` after `idle_patience_seconds`.
2. The callback returns quietly unless the session is `"waiting for order"` and the
   epoch matches. It appends the next installed app, logs, records `driver_app_opened`,
   and schedules itself again for the following app.
3. Edge case: the timer may fire while the driver is `"considering order"`. Keep
   `next_app_deadline`; in `_resolve_offer`, when the driver returns to waiting and the
   deadline has passed, open the next app immediately. Do not rely on the timer alone.
4. On ride completion (`_advance_ride` terminal branch) and when a driver goes online
   again, reset `open_platforms = [preferred]`, `idle_since = now`, bump `idle_epoch`,
   and schedule the idle timer again. Shift end is unchanged.
5. Offers carry `payout = platform.driver_payout(price, now)`; acceptance uses it.
6. Driver online time per platform is recorded as spans `(platform, start, end)` on the
   session (a list of open intervals), alongside the overall online span, so reporting
   can show per-platform supply without double counting in the whole-market view.
7. Trait default: `idle_patience_seconds` 600. Tests: a driver with one app never opens
   another; the second app opens exactly at the patience deadline; a ride completed on
   Blot returns the driver to Rebu only; a driver considering an offer at the deadline
   opens the next app when the offer is rejected; reservation invariant holds across
   platforms (two orders on two platforms cannot both hold the same driver).

## Milestone 4 — discounts and bonuses

1. Implement the incentive windows from the domain model. `Platform.rider_price` and
   `Platform.driver_payout` take the current simulated time.
2. Quotes carry `list_price`, `discount`, and `price`. Offers carry `payout`. Order
   completion records `fare`, `discount`, and `driver_payout`.
3. Summary and `summary.json` add per-platform revenue (fares paid), discount spend,
   bonus spend, and completed orders; the console summary prints one line per platform.
4. Tests: a 20% Flyt discount during 08:00–10:00 lowers only Flyt quotes inside the
   window; a flat Blot bonus raises Blot acceptance probability at the reference fare;
   windows outside the run have no effect; overlapping windows use the greater value.

## Milestone 5 — the market changes over time

Two mechanisms, both recorded as market events so the dashboard can show installed
base and preference over time.

**Scheduled by the scenario**

1. `schedule_app_install(at, person_id, platform, position="last")`,
   `schedule_app_uninstall(at, person_id, platform)`, and
   `schedule_preference(at, person_id, platform)` (moves the app to the front). They
   validate ids and names up front like session scheduling and append to
   `session_schedule` under their own `type`. Uninstalling the app a session is
   currently using takes effect when the session ends.
2. `platforms.sample_adoption_times(person_ids, windows, duration_seconds, seed)`
   mirrors `WeeklyDemandProfile.sample_session_times`: each `AdoptionWindow` gives a
   daily hazard for an uninstalled person to install; the helper returns `(person_id,
   at_seconds)` pairs for a scenario to schedule. This models marketing pushes and app
   launches (Flyt launching on day 8, say) without any in-run scanning.

**Driven by experience inside the run**

3. Rider experience per platform: after each session, for every platform opened, update
   `experience[p] = 0.8 * experience[p] + outcome`, where outcome is `+1` for a completed
   ride on `p`, `-1` when `p` showed no drivers, an unacceptable quote, or an order that
   no driver accepted, and `0` otherwise. If a non-preferred installed app's experience
   exceeds the preferred app's by `preference_margin` (default 1.0), move it to the front
   and record `preference_changed`.
4. Rider adoption: when a session ends without a ride, draw once per uninstalled
   platform with `platform.adoption_probability("rider", now)`; success installs it as
   the last app and records `app_installed`. Default probability is zero, so nothing
   happens unless the scenario sets adoption windows.
5. Driver earnings per platform: at shift end, compute payout earned per hour online on
   each platform during that shift and update `earnings_per_hour[p]` with the same 0.8
   decay. Re-rank with the same margin rule, expressed as a fraction of the preferred
   app's earnings (default 0.2). Driver adoption: when the idle timer has opened every
   installed app and the driver is still idle after one more `idle_patience_seconds`,
   draw with `platform.adoption_probability("driver", now)`.
6. All draws in this milestone use the new market RNG. Tests: scheduled install takes
   effect at the exact second and the next session opens it; scheduled preference moves
   the app to the front; an uninstalled app is never opened; a rider repeatedly stranded
   on Rebu but served on Blot switches preference after the margin is crossed and not
   before; zero adoption probability yields no installs; a run with two platforms and
   the same seed reproduces `market_history` exactly, before and after adding a draw in
   the decision RNG.

## Milestone 6 — population generation and a three-platform scenario

1. `population.py` (new): `assign_rider_profiles(ids, platforms, portfolio_shares,
   preference_weights, traits, seed)` and the driver twin. `portfolio_shares` maps
   app count to share (riders `{1: .55, 2: .35, 3: .10}`, drivers `{1: .40, 2: .40, 3: .20}`
   as defaults); `preference_weights` picks the preferred app (`Rebu .55, Blot .35,
   Flyt .10`); remaining apps are drawn by the same weights without replacement.
   `traits` gives uniform ranges: rider `switch_price_ratio` 1.15–1.6 and
   `switch_eta_seconds` 360–900; driver `idle_patience_seconds` 300–1200. Use one
   `random.Random(seed)` separate from the demand sampler.
2. `scenarios/scenario_three_platforms.py`: four weeks on the realistic-week template
   (30 drivers, 3,500 riders per week, same peaks). Rebu is the incumbent with the
   reference tariff, Blot undercuts it by 10% per kilometre, Flyt starts with 10%
   preference and a small installed base, launches a 25% rider discount and a flat
   driver bonus in week 2, and has a rider and driver adoption window from week 2 on.
   Report installed base and completed-order share per platform per week in the
   console summary.
3. Keep `scenario_100k_week.py` on the single default platform so its calibration
   holds; add a `platforms=` option only if it stays within its targets.
4. Tests: population shares match the requested mix within the sampled tolerance and
   are reproducible; the scenario runs to completion with count conservation per
   platform (orders created = completed + canceled + active, for each platform).

## Milestone 7 — reporting and dashboard

1. Every dashboard observation gains a `platform` field (`null` for whole-market
   spans). Rider sessions are attributed to a platform at each `app_opened`, so the
   per-platform session-to-order rate is "orders on this app / times this app was
   opened", while the whole-market rate keeps its current definition. Document both in
   `config.json` definitions.
2. `metrics.aggregate_intervals` and the JS `aggregate` accept an optional platform
   filter. Whole-market rows use the untagged driver-online spans; platform rows use the
   tagged ones. Per-platform utilization is active seconds of that platform's orders over
   online seconds on that platform.
3. Dashboard: a platform selector (All, Rebu, Blot, Flyt) next to the interval control
   that applies to all cards and charts; a new stacked "Completed-order share" chart;
   an "Installed base" chart built from the initial counts in `config.json` plus
   cumulative `app_installed` / `app_uninstalled` events from the run start (this one
   ignores the time selection's left edge, and says so in the chart note).
4. Extend `metrics.csv` and the CSV download with `platform` as a column, one block of
   rows per platform plus the whole market.
5. README: a "Platforms" section covering the domain model, trait defaults, incentive
   windows, adoption windows, the experience rules, the new events, and the new
   scenario. Update the layout table and the run-folder contents.
6. Tests: `test_reporting.py` gains a platform-filtered aggregation case that matches
   the Python series at every interval, a dashboard JS test for the selector, and a
   check that whole-market driver hours never exceed the sum of per-platform hours
   while multi-homing makes the sum larger.

## Acceptance scenarios

| Scenario | Required outcome |
| --- | --- |
| Any existing scenario, no `platforms` argument | Identical summaries and `events.jsonl` records (apart from the added `platform` field) to the frozen baseline. |
| One rider, three apps, forced order, Rebu expensive and Blot cheap | Rider opens Rebu, switches to Blot after the delay, orders on Blot; Flyt never opened. |
| One rider, preferred app has no drivers | Falls through installed apps in order; leaves only when none has a driver. |
| Driver with two apps, no demand | Second app opens exactly at the patience deadline; a later Blot ride returns them to Rebu alone. |
| Two orders on different platforms, one multi-homed driver | The driver is reserved by at most one; the other order moves to its next driver. |
| Flyt discount window | Flyt quotes are cheaper only inside the window; revenue and discount spend reconcile. |
| Blot driver bonus window | Blot acceptance rises inside the window; payouts reconcile with fares plus bonuses. |
| Scheduled install at t | The person's next session opens the app; earlier sessions do not. |
| Experience-driven preference | Preference changes only after the margin is crossed; the event is recorded once. |
| Same seed and configuration, repeated runs | Identical `market_history`, including installs and preference changes. |
| Four-week three-platform scenario | Runs without exceptions; per-platform count conservation; Flyt's share rises after its incentives start. |

## Completion and handoff

- Keep routing, commissions, surge pricing, and a UI outside this phase unless
  separately requested; note where each would attach (`Platform.driver_payout`,
  `Platform.rider_price`, and the dashboard selector).
- Run `python3 -m unittest discover -s tests -v`, `node --test tests/test_dashboard.js`,
  and `git diff --check` after every milestone. Rerun the 100k week once at the end and
  record its numbers.
- Record any deviation from the guiding decisions, the measured runtime of the
  three-platform scenario, and the trait and share defaults finally chosen.
