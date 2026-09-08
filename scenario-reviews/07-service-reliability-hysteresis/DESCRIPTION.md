Scenario 7: Service Reliability Hysteresis and Price vs. ETA Trade-offs (Intermediate)
Business or Research Question
Does sustained pickup unreliability cause permanent brand abandonment ("hysteresis"), where riders who switch to an expensive, reliable platform refuse to return to a budget competitor even after service is restored?
Starting Conditions
 * Duration: 5 simulation days (120 hours).
 * Population: 200 riders, 35 drivers.
 * Platforms: Platform Budget and Platform Reliable.
 * Participant Groups:
   * Riders: Start with a stated preference for low price. 100% have both apps installed.
   * Drivers: 25 drivers assigned to Reliable (due to guaranteed shifts), 10 drivers multihoming across both platforms.
Description of Events and Rules
Platform Budget charges $1.00 base + $1.00 per unit. Platform Reliable charges $3.00 base + $1.80 per unit.
Riders initially default to requesting rides on Platform Budget. However:
 * Phase 1 (t = 0 to t = 48): Due to limited driver supply on Budget, rider wait times frequently exceed 12 minutes, and cancellation rates exceed 30%.
 * Rider Fatigue Rule: If an individual rider experiences two consecutive failed trips (wait time > 12 minutes or driver cancellation) on Budget, that rider switches their default preference to Reliable for all future queries.
 * Phase 2 (t = 48 to t = 120): Budget injects 20 exclusive synthetic drivers, bringing its wait times down to an average of 4 minutes.
 * Hysteresis Rule: Riders who switched to Reliable during Phase 1 do not automatically revert to Budget unless Reliable fails them or Budget actively courts them with a push notification (which does not occur).
Observable Checks
 * Verify that individual rider state machines track consecutive failure counters on Platform Budget.
 * Confirm that during Phase 1, riders who log two consecutive failures transition their primary request generator to Platform Reliable.
 * Verify that during Phase 2 (t = 48 to t = 120), despite Budget's average pickup ETA dropping below Reliable's, the cohort of riders who converted to Reliable in Phase 1 continue to submit their first-choice queries to Reliable.
 * Confirm that overall system metrics reflect market share divergence between gross app installs (100% for both) and daily active query volume.
Main Modeling Challenge or Ambiguity
Investigate whether riders can maintain historical memory and individual brand affinities that persist across days, or if rider dispatch decisions are re-evaluated in every tick as stateless utility calculations.
