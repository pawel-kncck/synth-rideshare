from main import Simulation

sim = Simulation(driver_count = 2, rider_count = 5, seed = 0, order_delay_seconds = 5, accept_delay_seconds = 3)

def bring_online(driver, location):
    driver_session = driver.go_online(location)
    driver_session.wait_for_order()

sim.schedule(0,     lambda: bring_online(sim.drivers[0], (7, 2)))
sim.schedule(1000,  lambda: sim.riders[0].start_session((0, 1),(2, 8)))
sim.schedule(3000,  lambda: sim.riders[0].start_session((0, 1),(2, 8)))
sim.schedule(5000,  lambda: sim.riders[0].start_session((8, 2),(0, 1)))
sim.schedule(7500,  lambda: sim.riders[0].start_session((1, 1),(15, 11)))

sim.run()