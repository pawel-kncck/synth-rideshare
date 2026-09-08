# Flyt grows in an established market

A plausible synthetic experiment, not a forecast or an empirically calibrated
city. The question is whether easier access, cheaper rides and better driver
payouts help a small platform win completed rides as its promotions taper.

## Market and people

- **30 days starting on a Monday**, plus two hours to finish outstanding rides.
- **1,000 recurring riders**, each requesting 16 trips over the month: 16,000
  intended trips, about 533 per day. Requests retain the preset's weekday morning
  and evening commute peaks and Friday/Saturday nightlife peaks.
- **50 drivers with one car each**, split across three eight-hour crews. Each
  driver works five days out of seven, with two consecutive rest days staggered
  within each crew. Between 10 and 13 drivers are scheduled at a time; accepted work
  can finish after shifts end. The month contains 1,076 shifts.
- A **10 × 10 km service area**, 25 km/h travel and a 30-second boarding time.
  Drivers begin shifts in fixed neighborhoods; trip endpoints use the existing
  synthetic grid generator. Traffic, home/work trip chains and weather are absent.
- All platforms initially charge **2 + 1.50 per km**, with **20% commission**.
  Money is in illustrative currency units, with 100 minor units per unit.

Riders initially divide into these groups; drivers use the same weights with
largest-remainder rounding to whole people:

| Group | Riders | Installed apps | First choice |
| --- | ---: | --- | --- |
| Rebu regulars | 450 | Rebu, Blot | Rebu |
| Blot regulars | 250 | Rebu, Blot | Blot |
| Early Flyt users | 100 | All three | Flyt |
| Comparison shoppers | 100 | All three | Rebu |
| Rebu-only users | 100 | Rebu | Rebu |

Flyt begins with 200 rider installs and 10 driver installs, including 100 riders
and 5 drivers who prefer it. These are initial preferences, not assigned market
shares. Every person is aware of all three apps.

## Flyt's intervention

| Days | Rider discount | Cap per ride | Driver bonus per completed ride | Flyt commission |
| --- | ---: | ---: | ---: | ---: |
| 1–7: baseline | None | — | None | 20% |
| 8–14: encourage trial | 25% | 4.00 | 1.50 | 15% |
| 15–21: encourage repeat use | 20% | 3.00 | 1.00 | 15% |
| 22–30: taper support | 10% | 2.00 | 0.50 | 15% |

Discounts apply to all eligible Flyt rides during each window. Flyt funds the
discount and bonus; rider discounts do not reduce the driver's commission-based
gross-fare payout. Committed quote/offer terms survive campaign boundaries.
Rebu and Blot keep their original tariffs throughout.

Riders and drivers can adopt a missing app at daily checkpoints. The underlying
daily hazard is 0.035 for riders and 0.06 for drivers, attenuated by friction
0.3 and increased by personal dissatisfaction. These are chosen experiment
parameters, not measured adoption percentages. Drivers who adopt register their
car through the explicit onboarding flow. Both control and treatment have the
same awareness and adoption parameters; campaigns do not directly grant
downloads or preferences.

People update scores from actual personal outcomes with learning rate 0.12,
a preference margin of 0.12 and a three-day switching cooldown. Riders experience
net fare, pickup waits and ETA errors; drivers experience payout, service
duration and offer opportunities. Riders tolerate a seven-minute pickup estimate
and may cancel after 15 minutes. Drivers have a 60% second-order acceptance
multiplier and reject private pickup delays above 15 minutes.

The expected mechanism is wider Flyt access, more opportunities to try the app,
and improved experienced value during the campaign. Completed rides emerge from
rider search, supply, matching and learning. There are no forced preference
changes or scripted completion-share targets.

## Run and measure

```sh
python3 scenarios/flyt_growth_month.py --seed 0
python3 scenarios/flyt_growth_month.py --seed 0 --control
python3 metrics.py logs/<campaign-run>/simulation.log --market-share-days 7
python3 metrics.py logs/<control-run>/simulation.log --market-share-days 7
```

Each execution prints its own log path. Use `--market-share-days 1` for daily
results. Reporting is a separate offline step and leaves the logs unchanged.

The control keeps the same population, realized trips, shifts, learning and
adoption, but omits Flyt's campaigns and commission reduction. The same seed
gives identical prepared people and schedules; the first seven days should
match exactly. Later outcomes may diverge through behavior.

**Market share means completed rides on a platform divided by completed rides
across all three platforms in the same period.** It does not mean app installs,
quotes, bookings or the fraction of all requests served. Periods start at run
start; the final weekly bucket is only days 29–30 plus the two-hour drain.
Compare days 1–7 with days 22–28 for equal-length, weekday-matched windows, and
report the final short bucket separately. Empty periods have undefined shares.

Read completion counts alongside shares, unserved requests, pickup waits and
platform contribution. A share increase can come from lost competitor rides
as well as additional Flyt rides. Subsidies may make contribution negative;
contribution excludes operating costs and taxes. The month ends with modest
support still active, so it does not establish retention after all subsidies
end. One seed demonstrates a possible trajectory, not an expected effect across
markets. Several seeds and a subsidy-free follow-up are separate experiments.

## Observed seed-0 results

Both full runs completed successfully on this machine with Python 3.9.6.
Prepared-input fingerprints and first-week period results matched exactly.

| Period | Flyt share, campaign | Flyt share, control | Flyt rides, campaign | Flyt rides, control |
| --- | ---: | ---: | ---: | ---: |
| Days 1–7 | 6.0% | 6.0% | 152 | 152 |
| Days 8–14 | 17.2% | 15.2% | 413 | 363 |
| Days 15–21 | 29.8% | 26.8% | 721 | 620 |
| Days 22–28 | 38.8% | 32.2% | 936 | 765 |
| Days 29–30 + drain | 41.4% | 35.8% | 294 | 235 |

Flyt gained 6.7 percentage points over the control in week 4. Much of the total
growth also occurred without incentives through adoption and learning. Both
runs ended with 682 Flyt rider installs and 47 Flyt driver installs, so the
campaign's additional ride share in this seed did not come from more installs.

| Whole-month outcome | Campaign | Control |
| --- | ---: | ---: |
| Completed rides, all platforms | 10,459 | 10,261 |
| Completed Flyt rides | 2,516 | 2,135 |
| Average pickup wait, all platforms | 6.82 minutes | 6.80 minutes |
| Requests left unserved | 5,541 | 5,739 |
| Flyt contribution after discounts and payouts | −1,746.07 | 4,678.07 |
| Full process elapsed time, including setup and logging | 191.68 seconds | 190.54 seconds |
| Event-processing time | 180.18 seconds | 179.10 seconds |

Contribution is in illustrative currency units, before operating costs and
taxes. Flyt buys additional share at a substantial margin cost in this example.
Neither run ended with active orders, live intents, online drivers or pending
events. Period completion counts reconcile to whole-run totals, and created
orders reconcile to completed plus canceled orders.

Generated artifacts from this validation are under the ignored `logs/` directory:

- Campaign: `simulation-20260908-233108-e5sbyedh/simulation.log`.
- Control: `simulation-20260908-233420-0lgprpf9/simulation.log`.
- Offline summaries: `flyt-growth-month-seed-0-metrics.json` and
  `flyt-growth-month-control-seed-0-metrics.json`.
- Additional adoption/contribution checks: `flyt-growth-month-seed-0-outcomes.json`
  and `flyt-growth-month-control-seed-0-outcomes.json`.

The raw logs contain full initial/final checkpoints and are approximately
395 MB and 391 MB respectively. The measurements above come from one run of
each variant, with no parameter tuning after observing their trajectories.
