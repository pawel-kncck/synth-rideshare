Scenario 3: Driver Cancellation Penalties and Completion Reliability (Straightforward)
Business or Research Question
Does imposing financial and dispatch lockouts for post-acceptance driver cancellations improve platform trip completion rates, or does it trigger driver abandonment when pickup distances are high?
Starting Conditions
 * Duration: 12 simulation hours.
 * Population: 120 riders, 25 drivers.
 * Platforms: Platform Strict (enforces cancellation penalties) and Platform Lenient (zero cancellation penalties).
 * Participant Groups:
   * Riders: Request rides randomly across the city, willing to wait up to 10 minutes (0.16 hours) for pickup before cancelling.
   * Drivers: 25 multihoming drivers who accept dispatches within a 4-unit radius. If a driver realizes post-acceptance that the pickup ETA exceeds 6 minutes, their default behavior is to cancel the acceptance if allowed without penalty.
Description of Events and Rules
Both platforms set fares at $2.50 base + $1.20 per unit, with a 15% platform take-rate.
 * Platform Strict rule: If a driver accepts a ride and subsequently cancels it, the platform docks $4.00 from their accrued balance and blocks them from receiving Strict dispatches for 30 simulation minutes.
 * Platform Lenient rule: Drivers can cancel accepted rides freely with zero financial or dispatch penalties.
Riders generate requests steadily. Drivers evaluate incoming dispatches from both platforms. When assigned a long-distance pickup on Platform Strict, drivers must weigh the cost of completing an unprofitable pickup against the $4.00 fine and temporary lockout.
Observable Checks
 * Verify that every driver cancellation on Platform Strict results in an immediate $4.00 deduction from the driver’s ledger balance and a 30-minute block on new dispatches from that platform.
 * Confirm that during a Platform Strict dispatch lockout, the driver remains fully capable of receiving and accepting dispatches from Platform Lenient.
 * Ensure that a rider-initiated cancellation (due to the 10-minute wait timeout) does not apply the penalty to the driver.
 * Check that the simulator records distinct metrics for "Dispatched," "Accepted," "Driver Cancelled," "Rider Cancelled," and "Completed" trips.
Main Modeling Challenge or Ambiguity
Examine whether the simulator supports stateful driver account balance deductions and asymmetric platform-level temporary bans without disconnecting the driver from the global physical simulation world.
