Scenario 10: Three-Way Platform War with Endogenous Solvency and Driver Vehicle Debt (Difficult)
Business or Research Question
In a three-way platform price war, does predatory subsidized pricing cause an entrant platform to collapse from capital exhaustion before it can bankrupt the incumbent, and how does fixed vehicle debt drive driver labor supply under falling rates?
Starting Conditions
 * Duration: 14 simulation days (336 hours).
 * Population: 300 riders, 60 drivers.
 * Platforms:
   * Platform Corp (Incumbent): Starting cash reserve of $50,000; charges 20% take-rate; baseline pricing.
   * Platform Burn (VC Entrant): Starting cash reserve of $15,000; charges 0% take-rate; subsidizes 50% of every rider fare.
   * Platform Coop (Driver-owned): Starting cash reserve of $2,000; charges flat 5% take-rate to cover software maintenance; passes 95% of fares to drivers.
 * Participant Groups:
   * Drivers: Each driver has a non-negotiable daily fixed vehicle lease cost of $35.00 deducted at midnight (t = 24, 48, \dots). If a driver's cumulative cash balance drops below -\$100.00, they enter personal bankruptcy, their car is repossessed, and they are permanently removed from the simulation.
Description of Events and Rules
Riders prioritize price above all else. Platform Burn is massively cheaper due to its 50% discount.
 * Capital Exhaustion Rule: Platform Burn must pay drivers their full market rate out of its corporate treasury while collecting only half the fare from riders. If Platform Burn’s cash reserve hits $0.00, it instantly declares bankruptcy: the app shuts down, all pending dispatches are voided, and riders/drivers are forced to migrate to Corp or Coop.
 * Driver Debt Dynamics: Drivers must earn at least $35.00 net per day to cover vehicle depreciation. When fares are low, drivers cannot afford to log off; they must work longer hours to meet their fixed daily debt threshold.
 * Coop Dividend: If Platform Coop accumulates a cash balance exceeding its operating reserve ($2,000), it disburses the excess as an end-of-week dividend to all drivers who completed at least 20 rides on Coop.
Observable Checks
 * Verify that Platform Burn's cash balance decreases monotonically on every completed ride by an amount equal to: \text{Driver Payout} - \text{Discounted Rider Fare}.
 * Verify that the exact hour Platform Burn's balance reaches \le \$0.00, all Platform Burn services terminate, active trip requests on Burn fail gracefully, and no further Burn dispatches occur.
 * Confirm that drivers with cumulative balances below -\$100.00 at any daily reconciliation point are permanently deactivated from all platform dispatch registries.
 * Verify that Platform Coop's weekly dividend disbursement decreases Coop's cash reserves while increasing the individual ledger balances of qualified drivers without altering trip-level pricing records.
Main Modeling Challenge or Ambiguity
Evaluate whether the simulator can sustain endogenous financial feedback loops—where participant economic survival (corporate solvency, driver debt, and vehicle repossession) feeds directly back into dynamic marketplace supply and demand curves over long time horizons.
