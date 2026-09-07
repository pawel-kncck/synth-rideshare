# synth-rideshare

A small discrete-event simulator of a ride-hailing marketplace, written in
plain Python with no dependencies beyond the standard library.

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
| `scenarios/` | Runnable scripts that set up a population and play out a day. |
| `plans/` | Phased implementation plans and design notes. |

## Running

Requires Python 3.9 or newer.

```sh
python3 scenarios/scenario.py        # play out the sample scenario
```

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

sim.run()                            # 06:00 to 23:00, printing each lifecycle step
```

`schedule_driver_session(at, driver_id, location, shift_seconds=None)` brings
a driver online at that time; without a shift length the driver stays online
until the run ends. `schedule_rider_session(at, rider_id, location, destination)`
has a rider appear wanting to travel. `sim.drivers` and `sim.riders` are the
lists of ids in the population. Scheduling a session for someone who will
still be in one at that time raises an error when the clock gets there.

`sim.run(seconds_per_hour=0)` skips the playback delay. `sim.advance_to(t)`
jumps straight to a simulated time with no delay or output beyond the
lifecycle prints.
