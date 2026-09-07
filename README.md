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
- **Automatic behavior** is opt-in. `order_delay_seconds` makes riders order
  after a successful search, and `accept_delay_seconds` makes drivers accept
  offers. Left unset, every decision is made by the calling script.

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

A scenario schedules when people appear, then hands control to the clock:

```python
from main import Simulation

sim = Simulation(driver_count=2, rider_count=5, seed=0,
                 order_delay_seconds=5, accept_delay_seconds=3)

def bring_online(driver, location):
    driver.go_online(location).wait_for_order()

sim.schedule(0,    lambda: bring_online(sim.drivers[0], (2, 2)))
sim.schedule(1000, lambda: sim.riders[0].start_session((2, 2), (5, 8)))

sim.run()                            # 06:00 to 23:00, printing each lifecycle step
```

`sim.run(seconds_per_hour=0)` skips the playback delay. `sim.advance_to(t)`
jumps straight to a simulated time with no delay or output beyond the
lifecycle prints.
