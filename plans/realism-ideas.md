# Twenty ideas for increasing realism

Design notes only; nothing here is implemented. Each idea names the current
simplification (with the code that makes it), the proposed change, and a
rough size. Ideas are grouped by theme and ordered roughly by payoff within
each group. "Small" is an afternoon, "medium" a few days, "large" a phase.

Keep the existing invariants: seeded determinism, straight-line kilometres
as the coordinate system, decisions made inside the clock, and synthetic
defaults documented as assumptions rather than market estimates.

## Geography and travel

### 1. Road-network detour instead of straight lines (small)

`calculate_duration` and `_search` use `math.dist`, so every trip is a
straight line. Real trips on a street grid are 20–40% longer. Add a
`route_factor` (default about 1.3) or Manhattan distance for a grid city;
use it for trip distance, pickup distance, fares, and travel time. Pickup
ETAs and fares both rise, which changes conversion and acceptance.

### 2. Time-of-day congestion and stochastic travel time (medium)

`speed_kmh` is a single constant. Add a speed profile keyed by local hour
(and weekday), for example 18 km/h in commute peaks and 40 km/h overnight,
sampled once per leg. Separately, draw actual travel time from a log-normal
around the quoted time, so drivers arrive early or late relative to the
ETA. Quoted ETA should stay the *expected* value; the difference between
quoted and actual pickup time becomes a reportable realism metric.

### 3. Spatial structure: hotspots with time-dependent flows (medium)

`scenario_realistic_week.py` samples pickup and destination as two random
grid points, so demand is uniform in space and uncorrelated with the clock.
Define named zones (CBD, residential rings, airport, nightlife strip) with
weights per peak: morning peaks draw origins from residential zones and
destinations from the CBD; evenings reverse; nightlife peaks originate in
the strip and disperse. Supply then piles up in the wrong place after each
rush, which is where real marketplaces struggle.

### 4. Realistic trip-length distribution (small)

Uniformly sampled grid pairs give a symmetric distance distribution with a
mode near 5 km on a 10 km map. Sample trip length from a log-normal
(median ~4 km, long tail to 20 km) and pick a destination at that distance
in a random or zone-weighted direction. Short trips dominate real data and
change fare mix, utilization, and driver acceptance of low fares.

## Demand generation

### 5. Poisson arrivals with a smooth intensity curve (medium)

`sample_session_times` distributes a fixed count using hourly step
multipliers, so every run has exactly N sessions and demand jumps at the
top of the hour. Replace with an inhomogeneous Poisson process whose rate
is a piecewise-linear or Gaussian-mixture curve, plus a day-level random
volume factor (for instance ±15%). Daily totals then vary, ramps are
gradual, and rare quiet or busy days occur.

### 6. Repeat riders with personal frequency and habitual routes (medium)

Every rider has exactly one session all week. Give each `Rider` a weekly
trip rate drawn from a heavy-tailed distribution (most ride once, a few
ride daily), a home/work pair for commute trips, and a small set of
recurring destinations. Schedule the next session only after the previous
one ends (the invariants already forbid overlap). This creates commuters,
regulars, and one-off users, all with different price and ETA tolerance.

### 7. Exogenous shocks: weather, events, holidays (medium)

Nothing external ever changes demand or speed. Add a schedule of shocks:
rain windows (demand ×1.4, speed ×0.8), stadium or concert events (a burst
of sessions originating near one point over 30 minutes), and holidays that
swap the weekday profile for the weekend one. Each shock is a seeded,
logged scenario input so runs remain reproducible.

## Rider behavior

### 8. Patience, waiting, and re-searching (medium)

`_decide_on_quote` is a one-shot: order or leave after `order_delay_seconds`.
Phase 2 milestone 3 already sketches the alternative. Let riders who see
no drivers or a long ETA wait and search again a bounded number of times,
with per-rider patience. Also impose a maximum acceptable pickup ETA in
search coverage; today any idle driver on the map counts as "available".

### 9. Rider cancellation after ordering (small)

Once an order exists the rider never leaves until dropped off or all five
offers fail. Add a cancellation hazard that grows with elapsed wait during
dispatch and during the driver's approach (for instance 2% per minute
after the quoted ETA passes), plus an optional cancellation fee. Cancelled
rides free the driver mid-approach and count as a distinct outcome.

### 10. Heterogeneous riders and drivers (small)

All riders share `rider_price_sensitivity` and all drivers share one
acceptance baseline. Draw per-person traits once at population creation
from documented distributions (Beta for baselines, log-normal for
sensitivities) and store them on `Rider` and `Driver`, as Phase 2
milestone 1 proposed. Aggregate rates stay near the current defaults but
the same quote now converts differently for different people, and
outcomes become person-correlated.

### 11. Outside options and product tiers (medium)

Riders judge fares only against `reference_price`. Give each rider session
an outside option (walk, transit, taxi) with its own cost and time, and
convert based on the difference. Optionally add product tiers (economy,
XL, premium) with separate multipliers and driver pools, so a rider can
downgrade instead of leaving.

## Driver behavior

### 12. Destination- and shift-aware acceptance (small)

`driver_acceptance_chance` sees only fare and pickup ETA. Real drivers
weigh trip length, direction, and remaining shift: a long trip ten minutes
before shift end is refused, a trip toward home at the end of the day is
taken eagerly. Include trip duration and time to `shift_over` in the score
and let an ending shift decline offers that would overrun it by more than
a tolerance.

### 13. Idle repositioning and cruising (medium)

After drop-off the driver sits at the destination until the next offer
(`_advance_ride` sets `driver_session.location = order.destination`). Add
an idle policy: after N idle minutes, move toward the nearest hotspot, the
driver's home zone, or the area with the best recent demand-to-supply
ratio, at driving speed with ongoing location updates. Dead-heading time
appears as a new state between active and idle in the utilization split.

### 14. Endogenous supply: drivers choose when to work (large)

Thirty drivers in three fixed eight-hour crews always show up. Give each
driver an earnings target, preferred hours, and a decision to go online
based on expected hourly earnings (recent fares per online hour, any surge
in effect). Add breaks after a few hours online and shifts that end when
the target is reached. Supply then responds to demand and pricing, which is
the core feedback loop of a real marketplace.

### 15. Boarding, no-shows, and driver wait limits (small)

Boarding is a fixed 30 seconds. Draw it from a distribution (median 60 s,
tail to 5 minutes), let the rider fail to show with a small probability,
and let the driver leave after a wait limit (for instance 5 minutes),
cancelling the order with a "rider no-show" reason. This adds a
cancellation class that is common in real data and currently impossible.

## Pricing, matching, and dispatch

### 16. Time-based fare components, minimums, and driver payout (small)

`calculate_price` is base plus per-kilometre. Add a per-minute component
(so congestion raises fares), a minimum fare, a booking fee, and a
commission rate. Rider decisions keep using the total fare; driver
decisions should use their *payout* (fare minus commission plus any tip),
which is the quantity they actually respond to.

### 17. Surge pricing from local supply and demand (medium)

Peaks change arrivals only; the README notes there is no surge. Compute a
surge multiplier per zone from the recent ratio of searches to idle
drivers, cap it, and apply it to the quote. Riders convert less, drivers
accept more and (with idea 14) come online more. Report surge over time
alongside conversion so the trade-off is visible in the dashboard.

### 18. Smarter dispatch: ETA ranking, radius limits, and batching (medium)

`_dispatch_order` picks the nearest idle driver by straight-line distance,
one offer at a time, up to five. Rank by estimated travel time (with ideas
1–2 that differs from distance), refuse to offer beyond a maximum ETA,
include drivers about to complete a drop-off nearby, and batch orders over
a short window (3–5 s) to assign several orders jointly. Optionally add a
broadcast mode where several drivers see the offer and the first
acceptance wins, as some markets do.

### 19. Driver response-time distribution and quality traits (small)

Every driver answers after exactly `accept_delay_seconds`. Draw response
time from a distribution with a tail past the 10 second timeout, so some
offers expire naturally rather than by explicit rejection. Add per-driver
reliability (probability of cancelling after accepting) and a rating that
riders can factor into cancellation and ordering decisions.

## Validation

### 20. Calibrate against public benchmarks and add distribution tests (medium)

All defaults are declared synthetic. Pick reference targets from public
sources (for example NYC TLC trip records for trip length and duration
distributions, published pickup-wait and driver-utilization figures) and
add a `tests/test_calibration.py` that runs the weekly scenario and checks
distribution shapes rather than point values: median trip length, pickup
ETA percentiles, share of sessions without supply, utilization by hour.
Add a sensitivity scan script that sweeps one parameter and plots the
response, so each realism change above can be judged by its effect on
these curves rather than by inspection.

## Suggested order

Cheap and high-impact first: 1, 4, 10, 16, 12, 15, 9. Then the structural
ones that interact: 3, 5, 8, 13, 17, 18. Then 2, 6, 7, 11, 19, and finally
14, which depends on 16 and 17. Keep 20 running throughout so each change
is measured against the same targets.
