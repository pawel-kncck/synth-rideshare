Scenario 8: Mid-Trip Regulatory Price Cap and Contract Immutability (Difficult)
Business or Research Question
When municipal regulators enforce an instantaneous price cap and commission ceiling, does the simulation engine protect the contract integrity of in-flight and pre-booked rides, or do retroactive repricing bugs cascade through the settlement ledger?
Starting Conditions
 * Duration: 24 simulation hours. The policy shock occurs precisely at t = 12.00 hours.
 * Population: 250 riders, 60 drivers.
 * Platforms: Platform Apex and Platform Zenith.
 * Participant Groups:
   * Riders: Generating high demand across an airport-to-downtown transit corridor.
   * Drivers: Multihoming across Apex and Zenith.
Description of Events and Rules
From t = 0 to t = 12, unconstrained market pricing applies. Both platforms charge a surge-inflated rate of $4.00 per unit with a 25% take-rate.
At t = 12.00, an emergency municipal order takes effect:
 * Maximum fare cap: No trip may be billed at more than $2.00 per distance unit (base fare locked at $2.50).
 * Commission ceiling: Platform take-rates are capped at a maximum of 10%.
Boundary Conditions to Test at t = 12.00:
 * Trip A: Dispatched at t = 11.45, picked up at t = 11.55, currently in transit at t = 12.00, finishes at t = 12.20.
 * Trip B: Dispatched at t = 11.58, driver en route to pickup at t = 12.00, pickup occurs at t = 12.05, finishes at t = 12.25.
 * Trip C: Dispatched at t = 12.01, entirely under the new regulatory regime.
The regulatory policy stipulates contract immutability: any trip dispatched prior to t = 12.00 must be billed and settled under the pre-regulation quoted terms. Trips dispatched at or after t = 12.00 must strictly adhere to the price cap and commission ceiling.
Observable Checks
 * Verify that Trip A settles at t = 12.20 using the original $4.00 per unit rate and 25% commission, with zero retroactive adjustment.
 * Verify that Trip B (accepted before the deadline, but picked up after) maintains the pre-regulation fare agreement rather than crashing the billing calculator mid-trip.
 * Confirm that all ride requests initiated at t \ge 12.00 quote no more than $2.00 per unit and deduct no more than 10% platform take-rate.
 * Confirm that no platform balance sheet records a negative transaction margin due to a driver being paid pre-cap rates while a rider is billed capped rates on the same ride.
Main Modeling Challenge or Ambiguity
Determine whether pricing and commission logic are bound to the trip entity as an immutable contract at the instant of matching, or if fares are evaluated dynamically at trip completion by querying current global platform policy settings.
