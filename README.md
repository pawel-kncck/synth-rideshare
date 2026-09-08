# synth-rideshare

A small discrete-event simulator of a ride-hailing marketplace, written in
Python. The simulation uses the standard library; interactive run reports
use Plotly.

The goal is a synthetic, deterministic model of how riders and drivers
interact over a simulated day: riders search for a ride, order, get matched
to the nearest available driver through a sequence of offers, and are driven
to their destination. Everything runs on a simulated clock, so a full day
plays out in milliseconds and the same seed always produces the same story.

## What is modelled

- **Drivers and riders** are persistent people with stable IDs. Each opens
  transient sessions: a driver goes online at a location, a rider starts a
  session with a pickup point and a destination.
- **Search** returns an immediate quote: distance, duration, price, and the
  ETA of the nearest waiting driver. It never reserves anyone.
- **Orders** are dispatched to the nearest waiting driver. A driver may reject
  an offer; rejection or the ten-second timeout moves it to the next driver,
  up to five distinct drivers in total.
- **Rides** progress automatically once accepted: drive to pickup, wait for
  boarding, drive to the destination, complete. The rider session ends at
  drop-off; the driver stays available.
- **Decisions happen inside the simulation.** After `order_delay_seconds`,
  a rider may order or decline the quote. Riders prefer lower fares and shorter
  pickup ETAs. After `accept_delay_seconds`, a driver may accept or reject;
  drivers prefer higher fares and shorter pickup ETAs. A response at or past
  the timeout expires instead. No available driver means the rider leaves.
  A driver stays online until the optional shift length runs out, finishing
  any ride in progress first.
- **Demand can follow weekly peaks.** A reusable weekly profile weights rider
  session arrivals toward weekday commutes and Friday/Saturday nights,
  including the hours after midnight. Conversion happens after those arrivals.

Coordinates are kilometres on a flat map, travel is straight-line at a
constant speed, and fares are a base amount plus a per-kilometre rate. These
are deliberately simple synthetic assumptions, not estimates of a real market.

## Layout

| Path | Contents |
| --- | --- |
| `main.py` | The whole simulator: actors, sessions, orders, offers, scheduler. |
| `behavior.py` | Price and pickup-ETA decision probabilities and parameter validation. |
| `demand.py` | Configurable weekly peak windows and seeded arrival sampling. |
| `metrics.py` | Exact time-interval aggregation of searches, driver time, and completions. |
| `reporting.py`, `report_template.html`, `report_dashboard.js` | Run exports and the offline dashboard with global time filtering. |
| `scenarios/` | Runnable scripts that set up a population and play out a day. |
| `plans/` | Phased implementation plans and design notes. |

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
python3 scenarios/scenario_100k_week.py  # ~95,000 completed rides/week, ~72% utilization
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

The **100k-rides week** scenario targets **90,000–110,000 completed rides** and
**70–75% weekly driver utilization**. With its defaults and seed `0`, the current
simulator produces **94,773 completed rides** and **72.12% utilization**:

- 30,000 riders make seven sessions each: 210,000 sessions sampled across the
  week using the same commute and nightlife peaks. Sessions are assigned in
  arrival order, rotating through the riders to space out their repeat trips.
- 555 drivers form three crews of 185, each working eight hours every day on
  the same 10-by-10 km map. Drivers finish accepted trips after shift end.
- Default fares, travel speed, and probabilistic rider/driver decisions apply.
  Only session demand and driver supply are calibrated to the targets.
- The run covers exactly Monday 00:00 to the following Monday 00:00 (168 hours),
  with no extra drain hour. Trips still in progress at the boundary are excluded
  from completions; their active and online time is counted only within the week.

Run it with `.venv/bin/python scenarios/scenario_100k_week.py`. It runs without
sleeping and writes the usual dashboard and exports to `logs/`, with hourly
charts by default. `build_scenario(seed=0, rider_count=30_000,
drivers_per_shift=185)` allows experiments; changed parameters, seeds, or model
behavior may move the outcomes outside the calibrated ranges. Utilization is
total active driver time / total online driver time, including shift overtime
within the week, rather than an average of hourly percentages.

A scenario only schedules when driver and rider sessions begin, then hands
control to the clock. Everything after that (search, ordering, offers,
acceptance, travel, boarding, drop-off, and session ends) is triggered from
inside the simulation and cannot be driven from outside.

```python
from main import Simulation

sim = Simulation(driver_count=2, rider_count=5, seed=0,
                 order_delay_seconds=5, accept_delay_seconds=3)

# Times are simulated seconds after the run starts (06:00 by default).
sim.schedule_driver_session(0,    sim.drivers[0], (2, 2), shift_seconds=8 * 3600)
sim.schedule_rider_session(1000,  sim.riders[0], (2, 2), (5, 8))

sim.run(time_scale=3600)              # 06:00 to 23:00; one simulated hour per runtime second
```

`schedule_driver_session(at, driver_id, location, shift_seconds=None)` brings
a driver online at that time; without a shift length the driver stays online
until the run ends. `schedule_rider_session(at, rider_id, location, destination)`
has a rider appear wanting to travel. `sim.drivers` and `sim.riders` are the
lists of ids in the population. Scheduling a session for someone who will
still be in one at that time raises an error when the clock gets there.

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
When the run reaches its end time, it prints a summary
of simulated and runtime duration, sessions, order outcomes, riders leaving
without a ride, completed trip distance and fares, average order-to-pickup
wait, and the log path. The summary is also saved in the log. Failed or
interrupted runs log the exception and do not print a completion summary.

The summary also reports total driver online, active, and idle hours, plus
utilization (`active / online`). Online hours sum all driver session durations
within the run, including sessions still open at its end. Active hours cover
driving to pickup, waiting for the rider, and driving to the destination,
including unfinished rides up to the run's end. Idle hours are online hours
minus active hours; they include waiting for orders and considering offers
before acceptance. With no online time, utilization is `n/a`.

Summary activity counts cover that call to `run()`; active counts show what
remains at its end time. Pending events and active sessions or orders are
left in place, so a later run can continue them. `sim.advance_to(t)` jumps
straight to a simulated time without delay; called outside `run()`, it does
not create a log or print a summary.

## Rider and driver decisions

The default baseline is **55% session-to-order** when a driver is available,
and **70% offer acceptance**, both at the reference fare and pickup ETA.
These are probabilities, not fixed quotas or bounds: observed rates also
depend on supply, fares, pickup distances, timeouts, and seeded random draws.
Sessions without available drivers count toward overall conversion and
never place an order. Rejected offers count separately from expired offers.

| `Simulation` parameter | Default | Meaning |
| --- | --- | --- |
| `rider_order_probability` | `0.55` | Baseline chance of placing an order. |
| `driver_acceptance_probability` | `0.70` | Baseline chance of accepting an offer. |
| `reference_price` | `10.0` | Fare in the model's currency units at the baseline. |
| `reference_eta_seconds` | `300` | Pickup ETA at the baseline. |
| `rider_price_sensitivity` | `1.0` | Strength of riders' preference for lower fares. |
| `driver_price_sensitivity` | `1.0` | Strength of drivers' preference for higher fares. |
| `rider_eta_sensitivity` | `0.5` | Strength of riders' preference for shorter pickup ETA. |
| `driver_eta_sensitivity` | `0.5` | Strength of drivers' preference for shorter pickup ETA. |

The model adjusts log odds with relative price and ETA changes:

```text
score = log(p0 / (1 - p0))
        + signed_price_sensitivity * (price / reference_price - 1)
        - eta_sensitivity * (pickup_eta / reference_eta_seconds - 1)
probability = 1 / (1 + exp(-score))
```

The price sign is negative for riders and positive for drivers. Riders use
the quote they saw; each offered driver uses their own travel time to pickup
and the quoted fare. The fare is the full trip price, with no commission or
driver payout model. Fares still use base fare plus distance charge; peaks
change arrival demand, with no automatic surge pricing.

Probabilities must be finite and in `[0, 1]`; sensitivities must be finite
and nonnegative; reference values must be finite and positive. A sensitivity
of zero disables that preference. Baseline endpoints 0 and 1 force decisions,
regardless of price/ETA. Set both baseline probabilities to `1` to reproduce
the previous always-order/always-accept behavior, subject to supply and
timeouts. Existing scripts now use the realistic defaults. All behavior draws
come from a private RNG seeded by `seed`, independent of IDs and scenario
sampling. Playback speed and reporting do not affect those draws.

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
| Rider sessions | Sessions started in the interval, showing demand peaks. |
| Rider and driver decisions | Session-to-order and offer acceptance, shown together. |
| Search coverage | Searches finding an eligible idle driver / all searches, as a percentage. |
| Driver utilization | Total active driver seconds / total online driver seconds, as a percentage. |
| Completed orders | Orders completed during the interval, assigned by drop-off time. |

The default interval is 15 minutes. Use `sim.run(time_scale=False,
interval_minutes=5)` to start with 5 minutes, or choose 5, 15, 30, or 60 minutes
in the report without rerunning the simulation. Completed orders can also
be displayed cumulatively. Hover shows search counts and driver-hour totals;
the download button exports the selected period at the chosen chart interval
as CSV. These controls are grouped at the top of the dashboard.

Intervals start at the current run's initial simulated time. Events on an
internal boundary belong to the following interval; events exactly at the
run's end belong to its last interval, matching the simulator's clock.
The final interval may be shorter. Driver sessions and accepted-order time
are split exactly across intervals, including rides still in progress.
Intervals with no searches or no online driver time show gaps, and undefined
percentages are empty in CSV and `null` in JSON. Whole-run percentages use
total counts/time rather than averaging interval percentages. Continued runs
count only newly observed searches and completions, and only driver time
within that call to `run()`.

In the filtered dashboard, chart intervals start at the selection's beginning.
Internal day/range boundaries are exclusive on the right; only the saved
run's final instant belongs to the last selected interval. Session and offer
filters select the cohorts started in the selected period, retaining their
outcomes observed through the saved run's end.

Each run folder contains:

- `simulation.log`: lifecycle messages and the console summary.
- `config.json`: run bounds, status, seed, behavior parameters, actual scheduled
  sessions, metric definitions, Plotly version, and source-file hashes.
- `events.jsonl`: individual search observations, driver online/active intervals
  clipped to this run, and completion events. Each line has a `type` field;
  records are grouped by type, not sorted by time.
- `metrics.csv`: interval data using the run's selected default interval.
- `summary.json`: whole-run totals for the chart metrics.
- `report.html`: the standalone interactive report.

Session-to-order is converted sessions / all sessions started in this run.
Offer acceptance is accepted offers / all offers created in this run, including
rejected, expired, canceled, and pending offers. Both rates use the outcomes
observed by run end, attributed back to the session-start or offer-creation
interval. Thus delays across interval boundaries cannot produce rates over
100%. Counts of undecided sessions and pending offers accompany these rates.
Continued runs count only their new session and offer cohorts; later outcomes
for previous cohorts remain in the event export but do not revise earlier
reports or enter the new cohort's ratios.

`events.jsonl` also contains session-start, rider-decision, offer-created,
and offer-resolved events. Decisions retain the price, pickup ETA, probability,
and outcome. `config.json` saves all behavior settings and scenario peak
parameters alongside the actual scheduled sessions (schema version 2).

Searches are recorded individually in `sim.search_history`, including repeated
searches by one rider. Coverage currently means an eligible idle driver
anywhere on the map; it does not impose a maximum pickup ETA. Reporting does
not change availability, scheduling, behavior, or random-number generation.
Failed or interrupted runs retain their log and configuration with a failed
status, without printing a success summary or producing a completion report.

Run the tests with `.venv/bin/python -m unittest discover -s tests -v`.
Run the dashboard selection, navigation, aggregation, and CSV checks with
`node --test tests/test_dashboard.js` (Node.js 18 or newer).
