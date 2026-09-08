Scenario 6: Asymmetric Geo-Fencing, Deadheading, and Suburban Supply Deserts (Intermediate)
Business or Research Question
Does an unconstrained city-wide platform suffer lower driver retention and earnings compared to a geo-fenced urban core competitor, due to uncompensated empty return travel (deadheading) from suburban areas?
Starting Conditions
 * Duration: 24 simulation hours.
 * Population: 200 riders, 40 drivers.
 * Platforms: Platform UrbanOnly (geo-fenced) and Platform CityWide (unrestricted).
 * Participant Groups:
   * Core Riders (120 riders): Located within coordinates (0,0) to (5,5). Trip destinations are 80% within the Core, 20% to the Suburbs.
   * Suburban Riders (80 riders): Located within coordinates (6,6) to (15,15). Trip destinations are 50% Suburban, 50% Core.
   * Drivers: 40 multihoming drivers starting inside the Core.
Description of Events and Rules
 * Platform UrbanOnly restricts pickups and drop-offs strictly to the Core zone ((0,0) to (5,5)). Requests with origins or destinations outside this boundary are rejected at query time.
 * Platform CityWide accepts any trip within (0,0) to (15,15).
 * Both platforms pay drivers $1.20 per loaded mile with a 20% take-rate. Neither platform compensates drivers for empty miles traveled while seeking rides.
Drivers dropped off in the Suburbs by CityWide face a behavioral choice: wait for an organic Suburban request on CityWide, or travel back to the Core unpaid ("deadhead") to regain access to UrbanOnly dispatches. Drivers track their own net earnings per total kilometer traveled (including empty relocation).
+------------------------------------+
| Suburban Zone (6,6) to (15,15)     |
| [CityWide Only]                    |
|                                    |
|         +----------------+         |
|         | Core Zone      |         |
|         | (0,0) to (5,5) |         |
|         | [CityWide &    |         |
|         |  UrbanOnly]    |         |
|         +----------------+         |
+------------------------------------+

Observable Checks
 * Verify that Platform UrbanOnly rejects 100% of ride queries whose pickup or drop-off coordinates fall outside (0,0) to (5,5).
 * Confirm that drivers located outside the Core zone never receive dispatch requests from Platform UrbanOnly until their physical coordinates re-enter (0,0) to (5,5).
 * Track empty vehicle distance versus passenger-loaded distance separately for every driver.
 * Check that deadhead transit time from Suburbs to Core accurately consumes vehicle availability without generating ghost fares.
Main Modeling Challenge or Ambiguity
Evaluate whether the simulator can model autonomous driver repositioning behavior (deadheading without an active dispatch) based on historical earning expectations in distinct spatial zones.
