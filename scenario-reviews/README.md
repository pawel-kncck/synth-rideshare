# Scenario review inputs

Ten user-provided scenarios, split into separate folders. Each folder contains the original scenario text in `DESCRIPTION.md` and instructions for a future LLM review in `README.md`. No scenario analyses are included.

A reviewer should select a folder, follow its README, and write `ANALYSIS-{MODEL}.md` in that same folder.

## Scenarios

1. [Driver Supply Elasticity Under Asymmetric Platform Take-Rates (Straightforward)](01-driver-supply-elasticity/README.md)
2. [Flat-Fee Bonus vs. Multiplicative Surge Under Distance Heterogeneity (Straightforward)](02-flat-fee-vs-multiplicative-surge/README.md)
3. [Driver Cancellation Penalties and Completion Reliability (Straightforward)](03-driver-cancellation-penalties/README.md)
4. [Cold-Start Market Entry via Subsidized Rider Vouchers (Intermediate)](04-cold-start-rider-vouchers/README.md)
5. [Cross-Platform Chained Dispatching and Queue Collisions (Intermediate)](05-cross-platform-queue-collisions/README.md)
6. [Asymmetric Geo-Fencing, Deadheading, and Suburban Supply Deserts (Intermediate)](06-geofencing-and-deadheading/README.md)
7. [Service Reliability Hysteresis and Price vs. ETA Trade-offs (Intermediate)](07-service-reliability-hysteresis/README.md)
8. [Mid-Trip Regulatory Price Cap and Contract Immutability (Difficult)](08-regulatory-price-cap/README.md)
9. [Cross-App Exploitation of Guaranteed Hourly Driver Earnings (Difficult)](09-guaranteed-hourly-earnings/README.md)
10. [Three-Way Platform War with Endogenous Solvency and Driver Vehicle Debt (Difficult)](10-platform-solvency-and-driver-debt/README.md)

## Runnable scripts and units

`scenarios/reviews/sNN_<slug>.py` (repository root, one per folder above)
builds each scenario's market with today's simulator and runs its
`DESCRIPTION.md` "Observable Checks" through `--check`; see the top-level
[README](../README.md#scenarios) for the CLI and `../plans/scenario-
readiness-plan.md` for the consolidated plan these scripts and their `notes`
cite. A platform's DESCRIPTION.md name (e.g. "Platform Alpha") maps to a
free-string id each script chooses itself (e.g. `"alpha"`) -- platform ids
are never required to be `rebu`/`blot`/`flyt`. Wherever a review says "unit"
or "coordinate unit" it means one kilometre; money is integer minor units
(`minor_units_per_major = 100`, so 100 minor = $1.00); `t` is hours after the
scenario's calendar origin, as in `at_hours=`.
