Scenario 5: Cross-Platform Chained Dispatching and Queue Collisions (Intermediate)
Business or Research Question
When multihoming drivers are permitted to queue an incoming ride while finishing an active trip, how do competing platforms prevent spatial conflicts, schedule miscalculations, and compounding ETA drift?
Starting Conditions
 * Duration: 8 simulation hours.
 * Population: 180 riders, 30 drivers.
 * Platforms: Platform Blue and Platform Green.
 * Participant Groups:
   * Riders: Generate continuous trip requests throughout the spatial grid.
   * Drivers: All 30 drivers multihome across both platforms. Each driver can serve 1 active ride and hold at most 1 queued ride in their forward schedule.
Description of Events and Rules
Both platforms implement "forward dispatch" / "chained queuing": when a driver is within 3 minutes of finishing an active ride, the platform may match them with a nearby pickup.
A driver is currently physically transporting a rider on Platform Blue. While 2 minutes away from drop-off, the driver receives:
 * A queued dispatch offer from Platform Blue for a pickup 0.5 units away from the current destination.
 * A concurrent queued dispatch offer from Platform Green for a pickup 0.8 units away from the current destination.
If the driver accepts Platform Green’s queued ride, Platform Blue must detect that the driver's queue slot is filled. Furthermore, if the active Blue ride experiences a delay (e.g., simulated traffic or long drop-off), Platform Green’s pickup ETA drifts backward. If Green’s prospective rider sees their pickup ETA increase by more than 4 minutes past the initial promise, they cancel.
Observable Checks
 * Verify that no driver ever exceeds the physical limit of 1 active ride plus 1 queued ride, regardless of which platform issues the offers.
 * Confirm that once a driver accepts a queued ride on Platform Green while completing a ride on Platform Blue, both platforms register the driver's queue state as locked/busy.
 * Ensure that when an active trip on Platform Blue ends, the driver's physical location at the moment of drop-off becomes the starting origin for the transit to Platform Green's queued pickup.
 * Verify that rider cancellation logic on Platform Green continuously evaluates real-time projected pickup ETA rather than static initial match estimates.
Main Modeling Challenge or Ambiguity
Assess how the simulator synchronizes driver physical commitments across competing platforms: can Platform Green observe that a driver is occupied on Platform Blue without having direct access to Platform Blue's internal dispatch telemetry?
