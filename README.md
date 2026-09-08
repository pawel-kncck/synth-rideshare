# synth-rideshare

A small discrete-event simulator of a ride-hailing marketplace, written in
Python. The simulation uses the standard library; interactive run reports
use Plotly.

The goal is a synthetic, deterministic model of how riders and drivers
interact over a simulated day or week: riders ask for a ride, order, get
matched to a driver through a sequence of offers, and are driven to their
destination. Everything runs on a simulated clock, so a full day plays out in
milliseconds and the same seed always produces the same story.

## What is modelled

- **One physical market, many platforms.** The marketplace engine owns
  riders, drivers, cars, and positions. Platforms (today only Rebu) are
  marketplace instances inside it with their own app sessions, quotes,
  orders, offers, and observations; none owns a copy of a person or a car.
- **Drivers and riders** are persistent people with stable IDs and installed
  apps. A driver has one car registered with the platform, starts shifts at a
  location, and opens the app to receive offers. A rider appears with a trip
  intent (origin and destination) and opens the app to get a quote.
- **Quotes** are the platform's frozen terms: distance, duration, price, and
  the estimated pickup ETA of its best local candidate. They reserve nobody.
- **Orders** are dispatched by the platform to the nearest untried eligible
  driver, one offer at a time, up to five distinct drivers. A driver may
  reject; rejection or the ten-second timeout moves the offer on.
- **Commitments and queues.** A driver may hold at most two accepted,
  unfinished orders market-wide. The first starts pickup travel at once; the
  second is queued and starts from the actual drop-off point of the first.
  Rebu offers to drivers with a free slot, including one still serving a Rebu
  ride, and estimates that pickup from its known remaining service.
- **Rides** progress physically: drive to pickup, wait for boarding, drive to
  the destination, drop off, settle. Positions come from one straight-line
  trajectory per car; an onboard rider follows it. Arrival is a physical
  event, never an expired estimate.
- **Decisions happen inside the simulation.** After `order_delay_seconds`,
  a rider may order or decline the quote; riders prefer lower fares and
  shorter pickup ETAs. After `accept_delay_seconds`, a driver may accept or
  reject; drivers prefer higher payouts and shorter displayed ETAs. A response
  at or past the timeout expires instead. No available driver means the rider
  leaves. A shift end stops new acceptances and goes offline once accepted
  orders are finished.
- **Money** is settled in integer minor units when a ride completes: the
  rider pays the quoted fare, the driver receives the offered payout, and the
  platform contribution is the exact residual. `commission_fraction` (default
  `0`) lets Rebu keep part of the fare.
- **Demand can follow weekly peaks.** A reusable weekly profile weights rider
  arrivals toward weekday commutes and Friday/Saturday nights, including the
  hours after midnight. Conversion happens after those arrivals.

Coordinates are kilometres on a flat map, travel is straight-line at a
constant speed, and fares are a base amount plus a per-kilometre rate. These
are deliberately simple synthetic assumptions, not estimates of a real market.

## Layout

| Path | Contents |
| --- | --- |
| `event_engine.py` | Generic discrete-event scheduler: clock, ordered queue, handler registry, cancellation, budgets, snapshots. No ride-hailing code. |
| `marketplace_engine.py` | The shared physical market: entities, commands, atomic acceptance and the two-order cap, trajectories, physical service, cancellation, settlements, scoped views and notifications, snapshots. |
| `main.py` | `Simulation`: the scenario API, run/report orchestration, Rebu's quote and dispatch policy, and the rider/driver decision behavior, composed on the engine. |
| `behavior.py` | Price and pickup-ETA decision probabilities and parameter validation. |
| `demand.py` | Configurable weekly peak windows and seeded arrival sampling. |
| `metrics.py` | Exact time-interval aggregation of quotes, driver time, and completions. |
| `reporting.py`, `report_template.html`, `report_dashboard.js` | Run exports and the offline dashboard with global time filtering. |
| `scenarios/` | Runnable scripts that set up a population and play out a day or a week. |
| `plans/` | Phased implementation plans and the architecture documents. |

## Running

Requires Python 3.9 or newer.

Install the reporting dependency once, then activate the environment:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
source .venv/bin/activate
```

```sh
python3 scenarios/scenario.py        # play out the sample scenario
python3 scenarios/scenario_100_riders.py  # 100 riders, 10 drivers, no playback delay
python3 scenarios/scenario_500_riders.py  # 500 riders, 10 drivers, no playback delay
python3 scenarios/scenario_realistic_week.py  # weekly peaks, 3,500 riders, 30 drivers
python3 scenarios/scenario_100k_week.py  # 30,000 riders, 555 drivers, one week
```

The 100- and 500-rider scenarios use seed `0` and a 10-by-10 km map. All ten
drivers start at 06:00; each rider searches once by 22:00. Both runs end
at 23:00 and use `time_scale=False`.

The realistic-week scenario starts Monday at 00:00, schedules 3,500 rider
sessions over seven days, and leaves one final hour for trips to finish.
Thirty drivers form three crews of ten; each works an eight-hour shift daily.
It uses the same 10-by-10 km map, seed `0`, and no playback delay. Every rider
has one session. Default peak windows and arrival multipliers are:

| Peak | Days when the window starts | Local hours | Relative sessions/hour |
| --- | --- | --- | --- |
| Morning commute | Monday–Friday | 07:00–09:00 | 2.5× |
| Afternoon commute | Monday–Friday | 16:00–19:00 | 2.8× |
| Nightlife | Friday and Saturday | 21:00–03:00 next day | 3.0× |
| Off-peak | All other times | — | 1.0× |

These windows and intensities are configurable synthetic assumptions. Create
a `WeeklyDemandProfile(peaks=(PeakPeriod(...), ...))` to change them; overlapping
windows use the greatest multiplier. Weekdays use Monday=0 through Sunday=6.
`sample_session_times(count, duration_seconds, seed=0, start_weekday=0,
start_hour=0)` returns sorted offsets in seconds. It samples a fixed total
number of sessions with relative hourly weights, including partial hours.
Match these calendar arguments to the scenario's clock; the weekly script
uses `run(start=0, end=169)` (end hours may exceed 24 for multi-day runs).

The **100k-rides week** scenario was calibrated, before the marketplace
engine existed, to 90,000–110,000 completed rides and 70–75% utilization.
With its defaults and seed `0`, the current simulator produces
**103,719 completed rides** and **71.94% utilization**:

- 30,000 riders make seven sessions each: 210,000 sessions sampled across the
  week using the same commute and nightlife peaks. Sessions are assigned in
  arrival order, rotating through the riders to space out their repeat trips.
- 555 drivers form three crews of 185, each working eight hours every day on
  the same 10-by-10 km map. Drivers finish accepted orders after shift end.
- Default fares, travel speed, and probabilistic rider/driver decisions apply.
  Back-to-back dispatch lets a driver accept a second Rebu order while still
  serving one, so more rides complete per online hour than the earlier
  idle-only dispatch produced.
- The run covers exactly Monday 00:00 to the following Monday 00:00 (168 hours),
  with no extra drain hour. Trips still in progress at the boundary are excluded
  from completions; their service and online time is counted only within the week.

Run it with `.venv/bin/python scenarios/scenario_100k_week.py`. It runs without
sleeping, takes about five minutes (every quote and dispatch scans all open
drivers with Rebu's informed estimate; no spatial index yet), and writes the
usual dashboard and exports to `logs/`, with hourly charts by default. `build_scenario(seed=0, rider_count=30_000,
drivers_per_shift=185)` allows experiments. Utilization is total physical
service time / total online time, including shift overtime within the week,
rather than an average of hourly percentages.

A scenario only schedules when driver shifts and rider trips begin, then hands
control to the clock. Everything after that (quotes, ordering, offers,
acceptance, travel, boarding, drop-off, settlement, and going offline) is
triggered from inside the simulation and cannot be driven from outside.

```python
from main import Simulation

sim = Simulation(driver_count=2, rider_count=5, seed=0,
                 order_delay_seconds=5, accept_delay_seconds=3)

# Times are simulated seconds after the run starts (06:00 by default).
sim.schedule_driver_session(0,    sim.drivers[0], (2, 2), shift_seconds=8 * 3600)
sim.schedule_rider_session(1000,  sim.riders[0], (2, 2), (5, 8))

sim.run(time_scale=3600)              # 06:00 to 23:00; one simulated hour per runtime second
```

`schedule_driver_session(at, driver_id, location, shift_seconds=None)` starts
a driver's shift at that time; without a shift length the driver stays on
shift until the run ends. `schedule_rider_session(at, rider_id, location,
destination)` has a rider appear wanting to travel. `sim.drivers` and
`sim.riders` are the lists of ids in the population. Scheduling a shift or
trip for someone who is still in one at that time raises an error when the
clock gets there.

`time_scale` is the number of simulated seconds per runtime second:

- `1` runs in real time.
- Values between `0` and `1` slow playback down: `0.5` takes two runtime
  seconds for each simulated second.
- Values above `1` speed playback up: `60` plays one simulated minute per
  runtime second. The default, `3600`, preserves the sample's fast playback.
- `False` runs as fast as possible without calling `time.sleep`.

Numeric zero, negative values, nonfinite values, and `True` are rejected.

Every `sim.run()` creates a unique timestamped folder in `logs/` under
the current working directory. Pass `log_dir="path/to/logs"` to choose another
parent directory; `sim.run_directory` and `sim.log_path` hold the latest run
folder and its `simulation.log` path. Lifecycle events go to this file.
When the run reaches its end time, it prints a summary of simulated and
runtime duration, shifts, trips, quote coverage, conversion, offer outcomes,
order outcomes (including orders queued behind another ride), riders leaving
without a ride, completed distance, rider payments, driver payouts, platform
contribution, average order-to-pickup wait, and the log path. The summary is
also saved in the log. Failed or interrupted runs log the exception and do
not print a completion summary.

The summary also reports total driver online, in-service, and idle hours,
plus utilization (`in service / online`). Online hours sum all shift
durations within the run, including shifts still open at its end. Service
hours cover pickup travel, waiting for the rider, and driving to the
destination, including unfinished rides up to the run's end. An accepted
order queued behind another ride adds no service time until its own pickup
travel starts. Idle hours are online hours minus service hours; they include
waiting for offers and considering them.

Summary activity counts cover that call to `run()`; active counts show what
remains at its end time. Pending events, open shifts, live trips, and
unfinished orders are left in place, so a later run can continue them.
`sim.advance_to(t)` jumps straight to a simulated time without delay; called
outside `run()`, it does not create a log or print a summary.

## Marketplace engine

`marketplace_engine.MarketplaceEngine` holds the authoritative market:
platforms, riders, drivers, cars, shifts, trip intents, quotes, orders,
offers, physical services, and settlements, all referring to each other by
id. Policies act only through commands (`start_shift`, `end_shift`,
`open_app`, `close_app`, `begin_intent`, `end_intent`, `issue_quote`,
`place_order`, `create_offer`, `respond_to_offer`, `cancel_order`); each
validates, applies one coherent transition, and then publishes notifications
scoped to a platform, a driver, or a rider. `Simulation` is one listener that
plays Rebu's policy and every participant's behavior.

The engine enforces what no policy can change: one physical ride per rider,
driver, and car at a time; one trajectory per car; at most two accepted,
unfinished orders per driver across all platforms, acquired atomically on
acceptance; queued pickups that start only from the actual drop-off; shift
ends and app closures that drain rather than drop commitments; and money
that conserves exactly on every settlement. Multiple platforms can hold
offers to the same driver; a platform learns only the outcome of its own
offers. `sim.engine` exposes the records, `platform_view()`, `driver_view()`,
`rider_view()`, `position_of()`, and `snapshot()`/`restore()` for
checkpoints taken together with the scheduler's snapshot. See
`plans/architecture/marketplace-engine.md` for the contract.

## Event engine

The clock and the event queue live in `event_engine.Scheduler`, a generic
discrete-event scheduler with no knowledge of riders or drivers. The
marketplace engine registers `offer.expire` and `service.advance`; the
simulation adds `driver_session.start`, `driver_session.end_shift`,
`rider_session.start`, `rider_session.decide`, and `offer.respond`. Payloads
name records by id plus a generation (a shift id, an offer id, a service leg
index), and handlers return quietly when the thing an event was about has
already ended, so a stale timer can never revive an order or move a car.

`sim.scheduler` exposes the engine: `now`, `pending()`, `pending_count`,
`processed_count`, `next_time()`, `snapshot()`, and the execution budget
(`max_events_per_time`, 100,000 by default). A handler that raises stops the
run with `HandlerFailure`, which names the event and chains the original
exception; `config.json` then records the failed event and the events
processed just before it. See `plans/architecture/event-engine.md` for the
contract and `event_engine.py` for the API.

## Rider and driver decisions

The default baseline is **55% trip conversion** when a driver is available,
and **70% offer acceptance**, both at the reference fare and pickup ETA.
These are probabilities, not fixed quotas or bounds: observed rates also
depend on supply, fares, pickup distances, timeouts, and seeded random draws.
Trips without available drivers count toward overall conversion and never
place an order. Rejected offers count separately from expired offers.

| `Simulation` parameter | Default | Meaning |
| --- | --- | --- |
| `rider_order_probability` | `0.55` | Baseline chance of placing an order. |
| `driver_acceptance_probability` | `0.70` | Baseline chance of accepting an offer. |
| `reference_price` | `10.0` | Fare in the model's currency units at the baseline. |
| `reference_eta_seconds` | `300` | Pickup ETA at the baseline. |
| `rider_price_sensitivity` | `1.0` | Strength of riders' preference for lower fares. |
| `driver_price_sensitivity` | `1.0` | Strength of drivers' preference for higher payouts. |
| `rider_eta_sensitivity` | `0.5` | Strength of riders' preference for shorter pickup ETA. |
| `driver_eta_sensitivity` | `0.5` | Strength of drivers' preference for shorter pickup ETA. |
| `commission_fraction` | `0.0` | Share of the gross fare Rebu keeps; the driver is offered the rest. |

The model adjusts log odds with relative price and ETA changes:

```text
score = log(p0 / (1 - p0))
        + signed_price_sensitivity * (price / reference_price - 1)
        - eta_sensitivity * (pickup_eta / reference_eta_seconds - 1)
probability = 1 / (1 + exp(-score))
```

The price sign is negative for riders and positive for drivers. Riders use
the quoted fare and ETA they saw; each offered driver uses the offered payout
and the platform's displayed pickup ETA. Fares are base fare plus distance
charge, rounded half up to minor units; peaks change arrival demand, with no
automatic surge pricing. Rebu's displayed ETA counts its own remaining
service for a driver already carrying a Rebu rider, then travel from that
destination to the new pickup.

Probabilities must be finite and in `[0, 1]`; sensitivities must be finite
and nonnegative; reference values must be finite and positive. A sensitivity
of zero disables that preference. Baseline endpoints 0 and 1 force decisions,
regardless of price/ETA. Set both baseline probabilities to `1` to reproduce
always-order/always-accept behavior, subject to supply and timeouts. All
behavior draws come from a private RNG seeded by `seed`, independent of IDs
and scenario sampling. Playback speed and reporting do not affect those draws.
Each draw is kept in `sim.decisions` with its probability and inputs.

## Run reports

Each completed run automatically creates `report.html`. Open the path printed
in the console (also available as `sim.report_path`) in a browser. The file
contains Plotly and all chart data, so the charts work offline and the HTML
can be copied or shared by itself. Generating charts happens after the
simulation finishes; reported runtime excludes report generation.

The **Week / Day / Range** selector sits above every metric card and chart.
Week and day modes list available simulated periods; previous/next arrows
move one period and disable at the run boundaries. Weeks follow Monday–Sunday
when the scenario supplies a starting weekday; otherwise they group seven
simulated days. Range selects inclusive start and end days, and its arrows
move by the range length, preserving that length at the run boundaries.
Partial first/last periods are clipped to the available run time. Single-day
runs open in Day mode; longer runs open on their first available week.

The selected period applies to all six metric cards, every chart, cumulative
completion totals, and the CSV download. Metrics are recomputed from embedded
observations, with driver time split exactly at selection boundaries and
ratios weighted by their counts/time. Cumulative completions restart at the
selection's beginning. Changing the chart interval preserves the selection;
changing the selected period resets chart zoom. Full-run source files remain
available through explicitly labeled links in the footer.

Five line charts share a zoomable simulated clock axis:

| Metric | Per-interval definition |
| --- | --- |
| Rider sessions | Trips started in the interval, showing demand peaks. |
| Rider and driver decisions | Trip conversion and offer acceptance, shown together. |
| Search coverage | Quotes finding an eligible driver with a free slot / all quotes, as a percentage. |
| Driver utilization | Total physical service seconds / total online seconds, as a percentage. |
| Completed orders | Orders completed during the interval, assigned by drop-off time. |

The default interval is 15 minutes. Use `sim.run(time_scale=False,
interval_minutes=5)` to start with 5 minutes, or choose 5, 15, 30, or 60 minutes
in the report without rerunning the simulation. Completed orders can also
be displayed cumulatively. Hover shows quote counts and driver-hour totals;
the download button exports the selected period at the chosen chart interval
as CSV. These controls are grouped at the top of the dashboard.

Intervals start at the current run's initial simulated time. Events on an
internal boundary belong to the following interval; events exactly at the
run's end belong to its last interval, matching the simulator's clock.
The final interval may be shorter. Shifts and physical services are split
exactly across intervals, including rides still in progress. Intervals with
no quotes or no online driver time show gaps, and undefined percentages are
empty in CSV and `null` in JSON. Whole-run percentages use total counts/time
rather than averaging interval percentages. Continued runs count only newly
observed quotes and completions, and only driver time within that call to
`run()`.

In the filtered dashboard, chart intervals start at the selection's beginning.
Internal day/range boundaries are exclusive on the right; only the saved
run's final instant belongs to the last selected interval. Trip and offer
filters select the cohorts started in the selected period, retaining their
outcomes observed through the saved run's end.

Each run folder contains:

- `simulation.log`: lifecycle messages and the console summary.
- `config.json`: run bounds, status, seed, behavior parameters, actual scheduled
  sessions, metric definitions, Plotly version, and source-file hashes.
- `events.jsonl`: quote observations, trip starts and endings, rider and
  driver decisions with their probabilities, offers created and resolved,
  shift and service intervals clipped to this run, completions,
  cancellations, and settlements. Each line has a `type` field; records are
  grouped by type, not sorted by time.
- `metrics.csv`: interval data using the run's selected default interval.
- `summary.json`: whole-run totals for the chart metrics.
- `report.html`: the standalone interactive report.

Trip conversion is converted trips / all trips started in this run. Offer
acceptance is accepted offers / all offers created in this run, including
rejected, expired, canceled, failed-acceptance, and pending offers. Both
rates use the outcomes observed by run end, attributed back to the
trip-start or offer-creation interval. Thus delays across interval boundaries
cannot produce rates over 100%. Counts of undecided trips and pending offers
accompany these rates. Continued runs count only their new trip and offer
cohorts; later outcomes for previous cohorts remain in the event export but
do not revise earlier reports or enter the new cohort's ratios.

Quotes are recorded individually in `sim.engine.quotes`, including repeated
quotes by one rider. Coverage means an eligible driver with a free order slot
anywhere on the map; it does not impose a maximum pickup ETA. Reporting does
not change availability, scheduling, behavior, or random-number generation.
Failed or interrupted runs retain their log and configuration with a failed
status, without printing a success summary or producing a completion report.

There is no unit test suite. At this stage every change may be a refactor,
and tests would freeze ad-hoc decisions into requirements. Check a change by
running the scenarios and reading the summaries and reports they produce;
`plans/architecture/marketplace-engine.md` lists the engine behaviors that
throwaway scripts check.
