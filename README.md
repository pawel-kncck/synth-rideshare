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
- **Orders** are dispatched to the nearest waiting driver. An offer expires
  after ten seconds and moves to the next driver, up to five in total.
- **Rides** progress automatically once accepted: drive to pickup, wait for
  boarding, drive to the destination, complete. The rider session ends at
  drop-off; the driver stays available.
- **Decisions happen inside the simulation.** A rider orders
  `order_delay_seconds` after a quote that found a driver, or leaves if none
  was available or no driver accepted. A driver accepts an offer
  `accept_delay_seconds` after receiving it; an accept delay at or past the
  offer timeout means the offer expires instead. A driver stays online until
  the optional shift length runs out, finishing any ride in progress first.

Coordinates are kilometres on a flat map, travel is straight-line at a
constant speed, and fares are a base amount plus a per-kilometre rate. These
are deliberately simple synthetic assumptions, not estimates of a real market.

## Layout

| Path | Contents |
| --- | --- |
| `main.py` | The whole simulator: actors, sessions, orders, offers, scheduler. |
| `metrics.py` | Exact time-interval aggregation of searches, driver time, and completions. |
| `reporting.py`, `report_template.html` | Run exports and offline interactive charts. |
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
```

The 100- and 500-rider scenarios use seed `0` and a 10-by-10 km map. All ten
drivers start at 06:00; each rider requests one trip by 22:00. Both runs end
at 23:00 and use `time_scale=False`.

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

## Run reports

Each completed run automatically creates `report.html`. Open the path printed
in the console (also available as `sim.report_path`) in a browser. The file
contains Plotly and all chart data, so the charts work offline and the HTML
can be copied or shared by itself. Generating charts happens after the
simulation finishes; reported runtime excludes report generation.

Three line charts share a zoomable simulated clock axis:

| Metric | Per-interval definition |
| --- | --- |
| Search coverage | Searches finding an eligible idle driver / all searches, as a percentage. |
| Driver utilization | Total active driver seconds / total online driver seconds, as a percentage. |
| Completed orders | Orders completed during the interval, assigned by drop-off time. |

The default interval is 15 minutes. Use `sim.run(time_scale=False,
interval_minutes=5)` to start with 5 minutes, or choose 5, 15, 30, or 60 minutes
in the report without rerunning the simulation. Completed orders can also
be displayed cumulatively. Hover shows search counts and driver-hour totals;
the download button exports the currently selected interval as CSV.

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

Searches are recorded individually in `sim.search_history`, including repeated
searches by one rider. Coverage currently means an eligible idle driver
anywhere on the map; it does not impose a maximum pickup ETA. Reporting does
not change availability, scheduling, behavior, or random-number generation.
Failed or interrupted runs retain their log and configuration with a failed
status, without printing a success summary or producing a completion report.

Run the tests with `.venv/bin/python -m unittest discover -s tests -v`.
