Scenario 1: Driver Supply Elasticity Under Asymmetric Platform Take-Rates (Straightforward)
Business or Research Question
When two platforms offer identical rider pricing and dispatch algorithms, does driver multihoming allocate active availability purely in proportion to net take-rate differences, or do friction and spatial distribution blunt supply rebalancing?
Starting Conditions
 * Duration: 24 simulation hours.
 * Population: 200 riders, 40 drivers.
 * Platforms: Platform Alpha (20% take-rate commission) and Platform Beta (10% take-rate commission).
 * Participant Groups:
   * Riders: Uniformly distributed across a 10x10 coordinate grid, generating 0.5 trip requests per hour on average. All 200 riders have both apps installed and pick whichever platform quotes the lowest ETA (or a random coin flip if equal).
   * Drivers: All 40 drivers have both apps installed and online. They have identical vehicle operating costs ($0.30 per coordinate unit traveled).
Description of Events and Rules
At t = 0, both platforms launch with identical base fares ($2.00 base + $1.50 per distance unit). Neither platform runs surge pricing or promotional discounts.
When a driver is idle, both apps broadcast dispatch offers. If dispatch requests arrive simultaneously, drivers evaluate the net payout (gross fare minus platform commission). Drivers prioritize dispatches from Platform Beta due to its 10% take-rate versus Alpha’s 20%. If an offer arrives while the driver is idle on only one app, they evaluate whether waiting for an offer on Beta yields higher expected earnings than accepting the immediate Alpha dispatch.
Observable Checks
 * Verify that for trips of identical distance, Platform Beta logs a driver payout exactly 12.5% higher than Platform Alpha (0.90 \times \text{fare} vs. 0.80 \times \text{fare}).
 * Confirm that when both platforms dispatch an offer to the same driver within the same matching tick, the driver rejects or ignores Alpha’s offer and accepts Beta’s.
 * Ensure zero rides are dispatched to drivers who are already physically serving an active trip on the other platform without an open queue slot.
 * Confirm that total completed rides across both platforms match the sum of individual platform completed ride counters.
Main Modeling Challenge or Ambiguity
Investigate how the simulator handles simultaneous dispatches to a multihoming driver: does it allow atomic evaluation across independent platforms within a single simulation tick, or does race-condition order-of-execution artificially favor the platform processed first?
