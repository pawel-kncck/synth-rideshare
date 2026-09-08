# Phase 3: Rebu, Blot, and Flyt marketplace

Planning document only. No simulator implementation is included in this change.

Build one shared physical marketplace containing three competing platforms:
**Rebu, Blot, and Flyt**. Keep one identity, location, and physical activity state
per person. Platforms determine which people can interact, what a trip costs,
what a driver earns, and which app each person chooses to use. Market share is
an outcome of these interactions, rather than a percentage assigned to orders.

The recommended starting assumptions are that Rebu represents the current
unnamed platform; riders check apps sequentially; and drivers can leave several
apps open while idle. Drivers have one pending offer or accepted ride globally.
That last rule is an explicit simplification: simultaneous app availability is
supported, but simultaneous competing offers to one driver are deferred. An
exclusive-switch driver policy should also be configurable for experiments.

1. **Use the existing event engine and correct its single-platform assumptions.**

   The current code already has persistent person IDs, transient sessions,
   immutable quotes, deterministic offer reservations, and an event scheduler.
   It does not yet have persistent person objects holding behavioral attributes.
   The main integration points are:

   | Existing location | Required change |
   | --- | --- |
   | `main.py`: `Simulation.__init__` | Register platforms and persistent rider/driver profiles, with separate random streams for new behavior. |
   | `calculate_price`, `SearchResult`, `SearchRecord` | Add platform, tariff version, quote identity, validity, and separate rider/driver economics. |
   | `_driver_is_eligible`, `_search`, `_dispatch_order` | Filter by installed and open apps, while preserving global driver reservations. |
   | `_start_rider_session`, `_decide_on_quote`, `_abandon_order` | Keep a trip request alive across bounded app searches and failed platform orders. |
   | Driver session lifecycle and `_advance_ride` | Start and invalidate idle-triggered app-opening events. |
   | `behavior.py` | Retain reusable probability helpers; add policies using person-specific attributes. |
   | `metrics.py`, `reporting.py`, `report_dashboard.js` | Replace the assumption of one rider decision per session; add platform attribution without duplicating people or time. |
   | `scenarios/` and `demand.py` | Reuse weekly demand generation; add explicit populations, platform policies, interventions, and repeated activity over multiple weeks. |

   Keep the event scheduler and physical ride lifecycle centralized. Do not run
   three independent `Simulation` instances: that would require synchronizing
   the same drivers, riders, trips, and clock across separate markets.

2. **Represent app ownership, preference, and current app use separately.**

   Introduce the following data, retaining the public lists of IDs used by the
   existing scenarios and adding profile dictionaries keyed by those IDs.

   | Object | Persistent or transient state |
   | --- | --- |
   | Platform configuration | Stable ID (`rebu`, `blot`, `flyt`), display name, launch time, versioned pricing, commission, and incentive schedules. |
   | Rider profile | Installed apps, preferred app and scores for other apps, price/ETA tolerances, sensitivities, search friction, patience, learning rate, and adoption propensity. |
   | Driver profile | Enabled apps, preferred app and scores, tolerance for time without an offer, payout/pickup sensitivities, learning rate, and adoption propensity. In the initial model, downloading a driver app enables it immediately; onboarding delays can be added later. |
   | Rider session | One trip intent: apps considered, quote snapshots, current platform attempt, elapsed search time, retry budget, and at most one active order. |
   | Driver session | One physical shift: open apps, location, offer/ride reservation, idle timestamps, and a generation number for pending choice callbacks. |
   | Order and offer | Owning platform, stable trip/session identity, immutable price or payout commitments, and the existing lifecycle state. |
   | Per-person platform experience | Observed prices, pickup times, failures, offer waits, payouts, exposure duration, and observation counts. |

   Installed apps constrain access; preferred app determines the normal first
   choice; open apps determine current participation. A download must not
   automatically make the new app preferred. A different platform winning one
   ride must not automatically become the person's preferred app either.

   Scenario population settings should support all seven nonempty subsets,
   independently for riders and drivers: Rebu, Blot, Flyt, Rebu+Blot, Rebu+Flyt,
   Blot+Flyt, and all three. Configure a preferred-app distribution conditional
   on each subset, so nobody prefers an app they do not have.

   Support both explicit profiles for small deterministic scenarios and seeded
   population segments for larger ones. Segments can correlate ownership,
   loyalty, sensitivity, and adoption propensity; do not require all attributes
   to be sampled independently. Draw stable personal traits once, rather than
   rerolling them on every trip or shift. Distribution weights must sum to one;
   use deterministic rounding and seeded assignment when exact segment counts
   are requested. Initial app ownership and preferences are inputs; initial
   completed-ride share is not implied by either distribution.

3. **Give each platform its own pricing and incentive accounting.**

   A first implementation needs independent base fare, distance rate, optional
   time rate, minimum fare, and scheduled multiplier for each platform. Preserve
   the current base-plus-distance calculation as the compatibility setting.
   Fare changes and campaigns are scheduled in simulated time. Automatic surge
   can later implement the same pricing interface, but is not needed initially.

   Keep one currency and define the calculation explicitly:

   ```text
   gross fare G = max(minimum fare,
                      multiplier * (base + per_km * km + per_minute * minutes))
   rider discount D = eligible fixed amount OR percentage of G, capped at G
   rider payment = G - D
   base driver payout = (1 - commission_rate) * G
   driver payout = base driver payout + eligible driver bonus B
   platform contribution = rider payment - driver payout
                         = commission_rate * G - D - B
   ```

   Discounts and bonuses are platform-funded in this model. A rider discount
   lowers the rider's decision price without lowering driver pay. A driver
   bonus increases the driver's decision payout without raising the rider's
   price. Contribution excludes operating costs and taxes; it is not profit.
   Use a consistent monetary rounding rule and store settled amounts in integer
   minor units. Default commission is zero in single-platform compatibility
   mode, preserving the existing driver response to full fares.

   The initial campaign scope should include start/end times, fixed or capped
   percentage rider discounts, fixed bonuses per completed ride, and eligibility
   by segment or newly adopted app. Campaign visibility is also explicit:
   people may discover an incentive in a quote/offer, or receive scenario-defined
   awareness before using the app. An unknown campaign cannot change choice.
   Treat overlapping campaigns as non-stacking by default: apply the greatest
   eligible rider discount and greatest eligible driver bonus, with stable ID
   ordering for ties. Export which campaigns actually applied.

   Freeze gross fare and rider discount in a quote until its stated expiry.
   Creating an order binds those terms to that order. Freeze the driver's base
   payout and applicable bonus in each offer; acceptance binds those terms to
   the ride. An accepted bonus remains payable if the campaign ends during the
   trip. Only completed rides settle money, exactly once; canceled orders do
   not consume a completed-ride entitlement. Quote expiry requires a new search
   and a fresh rider decision, never a silent price substitution.

   Use half-open campaign windows `[start, end)` and policy lookup by simulation
   time, so boundaries do not depend on callback insertion order. Quotes and
   offers issued before a boundary retain their commitments for their validity
   period. Record policy versions and campaign IDs on those snapshots.

   Aggregate campaign budgets, first-N redemption limits, and target-based
   bonuses such as “complete 20 rides” are extensions, not requirements for the
   first version. They need explicit reservation/release rules to honor quoted
   incentives under concurrent demand; never implement a cap by silently
   reducing an already committed discount or bonus at settlement.

4. **Make rider choice a bounded search across apps for one trip.**

   The default flow is:

   ```text
   Trip intent -> open preferred installed app -> receive its quote
      -> acceptable: choose whether to order or leave
      -> price/ETA dissatisfaction or no supply: consider another installed app
      -> compare observed, valid quotes -> order one platform or leave
      -> dispatch failure: finish that order, then try another within the budget
      -> accepted ride: stop all app searches -> complete the trip
   ```

   Price sensitivity and ETA sensitivity should be separate personal traits.
   Express “too expensive” relative to a trip reference fare based on distance
   and duration, with a personal acceptable-price ratio. Keep that reference
   independent of the platform's current tariff or discount. Express the ETA
   tolerance in seconds. A configurable smooth response can turn excess above
   either tolerance into the probability of opening another app. For example,
   a bounded response of `1 - exp(-sensitivity * normalized_excess)` is zero at
   or below tolerance and increases with dissatisfaction. No supply and failed
   dispatch trigger trying an unvisited installed app when patience permits.

   Choose the next unvisited app using stored preference and known incentives,
   without inspecting quotes from apps the person has not opened. Each new app
   takes a configurable positive opening/decision delay. People can return to
   the best previously observed quote if it is still valid. Search never
   reserves a vehicle; supply must be checked again when dispatching an order.

   Keep the search trigger distinct from the final purchase decision. Among
   considered quotes, use a common utility scale combining platform preference,
   net rider price, pickup ETA, and search friction, with an outside option of
   leaving without a ride. Draw the outside option once per trip and reuse
   platform taste draws across retries. Repeatedly checking the same offer
   must not grant independent extra conversion chances. Additional apps can
   improve conversion by providing better options, not merely more coin flips.
   Preserve the existing decision function in single-platform compatibility
   mode and test its probability endpoints explicitly.

   Bound distinct app openings, quote refreshes, order attempts, and total trip
   search time separately. A useful first policy permits at most one order
   attempt per installed app, up to three platforms. A retry uses a positive
   delay. Enforce an explicit terminal exit when patience or all options are
   exhausted. Once a driver accepts, do not allow platform switching mid-ride.

   `_abandon_order` currently ends the rider session immediately. It must instead
   release the failed order and return to the rider's choice policy when another
   attempt is allowed. Use session/attempt identities on callbacks, so an old
   decision cannot order a newer quote or revive a finished trip.

5. **Let drivers expand app use when time without an offer becomes excessive.**

   At shift start, a driver normally opens their preferred enabled app. At ride
   completion, the default policy starts a new waiting episode with the preferred
   app; alternative apps are opened again if the driver waits too long. Allow
   scenarios to retain all previously opened apps between rides as an alternative
   policy. Keeping these choices explicit makes their supply effects measurable.

   Track two different clocks. `idle_started_at` measures time from shift start
   or ride completion until the next accepted ride. The no-offer clock measures
   time since becoming available or since the most recent offer arrived. The
   user's requested switching trigger is the second clock: receipt of an offer
   resets it even if the driver rejects the offer. Record time to first offer
   after drop-off separately from time to next acceptance. Opening another app
   resets neither clock.

   Sample a personal no-offer tolerance and schedule an event when it expires.
   A more impatient driver opens alternatives sooner; announced bonuses and
   learned platform experience affect which alternative they choose. If an
   offer is pending when a choice event fires, defer the choice until the offer
   resolves; accepted rides suppress it. After rejection or expiry, resume
   waiting against the latest offer timestamp. If further apps remain, schedule
   the next expansion after a positive additional delay. With all enabled apps
   open, do not schedule a repeated app-opening loop.

   The default multi-app policy adds the next app to `open_platform_ids`.
   Exclusive-switch mode replaces the previous open app. Neither action changes
   the driver's physical location, creates another shift, or transfers a ride.
   Receiving and accepting offers still uses payout and pickup sensitivity; idle
   time controls app participation rather than replacing the acceptance policy.

   An eligible driver must have the order's platform enabled and open, be within
   an active shift, and have neither a global pending offer nor a current ride.
   Reserve them atomically when dispatch creates an offer, making them unavailable
   to every platform until it is rejected, expired, canceled, or the accepted
   ride completes. Keep the existing nearest-driver and stable-ID tie rules;
   offer reservation conflicts resolve through deterministic event ordering.
   Preserve the five-distinct-driver limit per platform order initially.

   Increment a session/idle generation on every relevant transition. Each
   scheduled choice checks that generation and the session's current state.
   Shift end prevents new app openings, cancels pending offers, and still lets
   an accepted ride finish. This extends the current guarded-callback scheduler
   without requiring polling every driver on every simulated second.

6. **Model downloads and preference changes on a slower timescale.**

   Run repeated trips and shifts for the same people over a configurable number
   of weeks. Do not regenerate their ownership or preferences each day. Hold the
   total person population and exogenous trip-intent/shift schedules fixed in
   the initial model; incentives affect conversion, app participation, and
   allocation within those schedules. Generating additional trip intents or
   longer shifts in response to incentives is a separate extension.

   Provide two complementary evolution mechanisms:

   | Mechanism | Intended use |
   | --- | --- |
   | Scheduled interventions | Platform launch, pricing changes, campaign changes, marketing exposure, or explicit app/preference changes for a named cohort. Useful for controlled experiments. |
   | Behavioral evolution | People download additional apps or revise preference because of exposure and their observed experience. Produces endogenous changes in market share. |

   Use a configurable daily checkpoint for adoption and preference updates,
   while continuing to record experiences when they happen. For adoption, give
   each missing platform a nonnegative rate based on the person's segment,
   exposure to the platform, download friction, and accumulated dissatisfaction.
   A rate has units of events per simulated day; convert it using
   `p = 1 - exp(-rate * elapsed_days)` so changing the update interval does not
   accidentally multiply adoption intensity. With several missing apps, use
   the combined rate to decide whether a download occurs and rate weights to
   choose its target; initially permit at most one download per checkpoint.
   Document that this cap introduces a coarse-time approximation at high rates.
   Unlaunched platforms are ineligible. Rates of zero freeze adoption.

   Dissatisfaction can accelerate adoption, but exposure must allow adoption
   even among people with only one app. If social diffusion is later enabled,
   use an explicit exposure model, not unrestricted knowledge of every driver's
   availability or every rider's outcome. The initial model supports downloads
   without uninstalling; membership is therefore nondecreasing. Optional app
   removal/account inactivity can be added separately if ownership churn is
   needed, preserving at least one usable app and existing ride commitments.

   Learn platform experience from a person's own observations. Rider signals
   include normalized net price, observed pickup ETA/actual wait, and fulfillment
   failures. Driver signals include no-offer waits, pickup/service duration,
   and payout. Normalize for trip length and observation time so a platform
   serving longer trips is not automatically considered better or worse.
   Maintain bounded, smoothed scores and observation counts. Preserve priors
   for unused apps; unknown experience is not equivalent to a perfect or failed
   service. Default learning should use observed rewards without an additional
   direct “promotion preference boost,” which would count the same benefit twice.

   For drivers, use actual offer opportunities to estimate wait and expected
   payout per cycle, including pickup, boarding, and ride duration. Waiting that
   ends because the driver accepts another platform's offer is censored, not a
   completed wait or a failure. Time busy on another platform is not eligible
   exposure time. A concrete first estimator is an offer-arrival rate from
   observed offers divided by eligible waiting exposure, stabilized by a
   configurable prior rate and prior exposure. Its inverse estimates waiting
   time; combine it with smoothed observed payout and service duration to estimate
   earnings per cycle-hour. This deliberately simple arrival-rate approximation
   incorporates exposure with no offer, instead of learning only from successful
   waits. Keep raw wait/censoring records to validate or replace it later.

   Update preferred app only when another enabled app's score exceeds the
   current one by a configurable margin, with a preference-change cooldown.
   Combine learned experience with stable loyalty and explicit campaign awareness
   on documented scales. Some people therefore remain loyal after a small
   improvement elsewhere, while more flexible people move sooner. A zero
   learning rate freezes experience-driven preference changes. Downloads and
   explicit scenario changes remain separately controllable.

   Compute checkpoint decisions against the same pre-update population snapshot
   and apply them together, avoiding person-iteration bias. New ownership and
   preference affect the next safe decision: a new rider search or an idle
   driver app choice. They never interrupt a pending offer or accepted ride.
   Log every download and preference transition with its cause. Scheduled
   interventions at a checkpoint take precedence before behavioral updates,
   and conflicting explicit updates to the same property must be rejected.

   The feedback chain to expose in results is:

   ```text
   price / discount / bonus changes
     -> different rider choices and driver app participation
     -> different eligible supply, pickup ETAs, and offer waits
     -> different completions and earnings
     -> updated experience, downloads, and preferences
     -> different platform choices on subsequent trips and shifts
   ```

7. **Make scenarios declarative enough to compare experiments.**

   Keep Python scenario entry points. Add validated configuration objects rather
   than another collection of positional constructor parameters.

   | Configuration group | Contents |
   | --- | --- |
   | Market | Seed, horizon, physical population, geometry, speed, weekly trip-intent profile, and shifts. |
   | Platforms | Rebu/Blot/Flyt IDs, launch times, separate tariffs and commission rates. |
   | Rider population | Seven ownership-subset weights, conditional preferences, segment-specific tolerances and choice traits. |
   | Driver population | Separate ownership/preference weights, no-offer tolerances, app retention/switch policy, and acceptance traits. |
   | Campaigns | Platform, effective window, discount or bonus, eligibility, and awareness rules. |
   | Evolution | Update cadence, adoption rates/exposure, memory and learning settings, preference margin and cooldown. |
   | Interventions | Time-indexed platform-policy or cohort changes, with explicit precedence. |

   Save realized profiles and scheduled intents/shifts as well as the settings
   that generated them. Generate repeated rider activity with enough spacing to
   avoid overlapping sessions; retain the current public API's explicit error
   for overlapping sessions. Do not silently drop a trip intent when testing
   an intervention that causes longer searches or rides.

   Add a small deterministic scenario for inspection and a multiweek experiment
   with a baseline period, a Blot discount period, a Flyt driver-bonus period,
   and a post-campaign period. Clearly label chosen segment shares, prices,
   tolerances, and campaign sizes as synthetic example inputs. Include a frozen
   ownership/preference control and an otherwise identical evolving-population
   run. An optional entrant variant launches Flyt partway through the run with
   no initial Flyt users and a scenario-defined awareness campaign.

   Compare configurations using the same generated people, intended trips,
   shifts, and seed set. Report absolute rides and unserved demand alongside
   share, and measure retention after incentives end. Directional effects are
   expected only with other conditions held fixed: a discount can attract more
   demand than available drivers can serve. Do not force a campaign to increase
   completed-ride share in every congested scenario.

8. **Separate market metrics from platform metrics and ownership metrics.**

   Completed-ride share is the primary market-share measure:
   `platform completions / completions across all three platforms` within the
   selected time interval. It sums to 100% when at least one ride completes,
   and is undefined when none complete. Also expose first-app share, order share,
   and gross-fare share as distinct quantities, each with its denominator.

   | Measure | Required interpretation |
   | --- | --- |
   | Trip demand and conversion | One rider session per physical trip intent; conversion means at least one order, counted once even if several platform orders fail. Fulfillment means a completed ride. |
   | Platform funnel | App visits/searches, quotes, order attempts, accepted orders, and completions. State whether denominators count visits or unique trip intents. |
   | Rider switching | Original preferred/first app, apps opened, reasons, final platform or unserved exit, total search time, and pickup wait. |
   | Driver supply | Unique physical drivers plus eligible/pending/serving drivers by platform; app-open counts alone do not represent available supply. |
   | Driver time | Global online/idle/active time counted once per physical shift. Active time and payouts belong to the serving platform. |
   | App exposure | Idle time during which each app was open and eligible; these platform intervals can overlap and must not be summed into global driver hours. |
   | Driver opportunity | Time to first offer after a trip, subsequent offer gaps, time to next accepted ride, and censored waits. |
   | Ownership and preference | Installed-app penetration per platform, the seven ownership combinations, preferred-app shares, downloads, and preference transitions over time. |
   | Economics | Gross fares, net rider payments, discounts, base payouts, bonuses, platform contribution, and driver earnings per physical online hour. |

   Preferred-app shares sum to 100% for each role. Installed-app penetrations
   can sum above 100%, as can platform app-open driver counts. Report unique
   active riders/drivers separately; they can use multiple platforms within one
   selected period. Avoid a platform utilization ratio using another platform's
   service time or an undefined allocation of shared idle time. Initially show
   global utilization and per-platform service hours and exposure separately.

   Add versioned events for app openings/closures, quote decisions, order
   attempts, first conversion, terminal trip outcomes, ownership/preference
   changes, and financial settlement. Distinguish a quote rejection from an
   unserved trip. Every platform interaction carries a platform ID and the
   stable trip/shift/person IDs needed for attribution.

   The existing Python aggregator decrements `undecided_sessions` on every
   `rider_decision`; the dashboard keeps only the last decision for a session.
   Both assumptions break with repeated app searches. Introduce exactly-once
   trip conversion and terminal outcome records, keeping individual quote
   decisions as diagnostic events. Resolve this before enabling rider retries.
   Preserve offer-created/resolved cohort accounting for platform acceptance.

   Extend the existing dashboard with a Market/Rebu/Blot/Flyt selector and
   comparison series using fixed platform colors. Combine that selector with
   the existing Week/Day/Range controls for every card, chart, and CSV export.
   Include incentive/policy change markers, share trends, ownership/preferences,
   switch reasons, offer waits, and economics. Recompute weighted ratios from
   counts and time; never average percentages or add overlapping platform counts.

   Record initial profile state plus timestamped changes so an arbitrary report
   range reconstructs ownership and preferences at its start; counting only
   downloads inside the selected range is insufficient. Version the export
   schema, save policy/seed derivation details and added source hashes, and keep
   profile snapshots separate from compact dashboard aggregates for large runs.
   Continuations retain current membership, experience, monetary commitments,
   RNG state, and future events. Each saved report includes its own starting
   state and only newly observed events/settlements; earlier reports stay fixed.

9. **Implement in increments with explicit acceptance gates.**

   | Milestone | Deliverable and gate |
   | --- | --- |
   | 1. Platform and population foundation | Add profiles and platform-tagged records, platform-aware search/dispatch, independent tariffs, and compatibility mode. One driver cannot be duplicated across markets; a Rebu-only run preserves existing behavior. |
   | 2. Incentive economics | Add campaigns, awareness inputs, immutable terms, and settlement. Demonstrate a discount changing rider price only, a bonus changing driver payout only, and exact financial reconciliation. |
   | 3. Rider search and funnel | Implement sequential app choice, bounded cross-platform attempts, and exactly-once trip accounting in Python and dashboard observations. Demonstrate preferred-first search and fallback after price, ETA, and dispatch failures. |
   | 4. Driver app participation | Implement idle clocks, app-opening timers, global reservations, and app retention policies. Demonstrate the same driver reachable through several apps but serving only one ride. |
   | 5. Evolution | Add exposure/adoption, observed-experience learning, preference hysteresis, and scheduled interventions. Demonstrate persistent people changing ownership and preference over several weeks with market share calculated from completions. |
   | 6. Comparative reporting and scale | Finish platform/time filtering and experiment scenarios. Verify accounting, deterministic continuation, Python/JavaScript metric parity, and measured performance at small, weekly, and multiweek scales. |

   Suggested new modules are `platforms.py` for platform configuration, pricing,
   campaigns, and money; `population.py` for profiles and seeded segments;
   `choice.py` for pure rider/driver choice calculations; and `evolution.py` for
   adoption and preference transitions. Keep lifecycle mutations in `main.py`
   and retain reusable probability primitives in `behavior.py`. Do not name a
   module `platform.py`, which would shadow Python's standard-library module.

   Keep old `Simulation(...)` calls working through an explicit internal
   Rebu-only compatibility configuration when no platform/population config is
   supplied. Existing fare arguments map to Rebu in that mode. New marketplace
   scenarios explicitly configure all three platforms and heterogeneous access;
   reject ambiguous combinations of legacy fare arguments and platform tariffs.
   Document the three-platform scenario as the entry point for this feature.

   Use separate stable random streams for profile generation, rider choice,
   driver choice, and evolution. Derive their seeds reproducibly, without
   Python's process-randomized `hash()`. The compatibility path must not consume
   extra decision draws. For comparative experiments, key choice randomness by
   stable intent/person/decision identity where practical, so unrelated new
   events do not shift every later person's random draw. Reporting and playback
   never consume behavioral randomness.

10. **Validate causal rules, concurrency, accounting, and evolution.**

    Add focused tests with explicit people and forced choices before larger
    statistical experiments. Required cases include:

    | Case | Acceptance criterion |
    | --- | --- |
    | Single-, two-, and three-app people | Only installed/enabled apps can be used; preferred app belongs to that set. |
    | Preferred rider app acceptable | Default opens it first; no automatic query of unseen competitors. |
    | High price, high ETA, unavailable supply | Each cause can independently trigger another app, subject to personal sensitivity and patience. |
    | All platforms unattractive | Trip exits within finite time; repeated quotes do not create extra independent conversion draws. |
    | Failed first platform order, successful second | Two orders, one rider session, one converted trip, one completion. |
    | One driver, two platform orders at the same instant | At most one global pending offer and one accepted ride; deterministic loser behavior. |
    | Driver idle threshold | Alternative opens at the defined no-offer threshold; offer receipt resets the offer clock, app opening does not. |
    | Stale rider/driver timer, shift end, ride acceptance | Old callbacks cannot reopen apps, duplicate orders, or revive finished sessions. |
    | Discounts and bonuses | Correct decision input changes; committed terms survive campaign end; canceled orders do not settle. |
    | Quote expiry and tariff boundary | An expired quote is refreshed and reconsidered; new quotes use new policy, committed orders retain old terms. |
    | Adoption and preference | Download does not force preference; repeated good/bad experiences can change it; zero rates freeze the corresponding process. |
    | All apps installed or platform not launched | No duplicate/impossible download; no unbounded no-op update events. |
    | Driver observed through multiple apps | Busy time is not counted as idle opportunity on another app; interrupted waits are marked censored. |
    | Same seed/configuration and split versus continuous run | Same behavioral outcomes and settlements, excluding report metadata and wall-clock runtime. |
    | Arbitrary report range and platform filter | Correct starting ownership, unique trip accounting, exact clipped physical time, weighted ratios, and matching Python/JavaScript results. |

    Check conservation independently of which random choices occurred:
    orders created = completed + canceled + active; rider sessions started =
    ended + active; global completions = sum of platform completions; total
    physical online time = idle + active time; and rider payments = driver
    payouts + platform contribution. Every trip can complete at most once.
    Zero denominators produce gaps, not invented zeros or shares.

    Validate configuration before scheduling: platform IDs and references,
    nonempty app sets, subset weights, bounded probabilities, finite monetary
    amounts and tolerances, commission in `[0, 1]`, strictly positive retry and
    evolution intervals, and coherent time windows. Retain current offer-timeout
    ordering and horizon behavior, including unfinished rides and campaign
    commitments carried into a later run.

    Run the existing Python and dashboard test suites, plus the new targeted
    cases, during implementation. Use several seeds for aggregate directional
    experiments, with frozen controls to separate immediate price/supply effects
    from subsequent learning and adoption. Do not preserve the current
    94,773-ride/72.12%-utilization calibration as a target for three competing
    platforms; it describes the current single-platform model.

    Start with scans of active eligible drivers and profile only after correctness.
    A per-platform eligible-driver index is the first optimization if warranted;
    every global reservation must update all relevant indexes. Daily evolution
    may scan the population once, but ordinary event processing must not scan
    all inactive people. Measure event counts, runtime, history/report size, and
    peak memory on the existing 100k-week scale before extending the horizon.

The first complete release covers all requested behavior: heterogeneous app
ownership, preferred-first usage, rider price/ETA switching, driver no-offer
switching, independent platform pricing, rider discounts, driver bonuses, new
downloads, learned preference changes, and market-share evolution. Real routing,
automatic competitive pricing, simultaneous offers to one driver, campaign
budget optimization, app uninstalling, and endogenous total demand/shift length
remain separate extensions.
