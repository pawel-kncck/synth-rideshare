import pyglet
from pyglet import shapes
import random

max_tick_count = 10

SQUARE_SIZE = 30
GRID_NUM_ROWS = 30
GRID_NUM_COLS = 50 
NUM_OF_RIDERS = 10
NUM_OF_DRIVERS = 3

class City:
    def __init__(self, num_rows, num_cols):
        self.num_rows = num_rows
        self.num_cols = num_cols
        self.grid = [[None for _ in range(num_cols)] for _ in range(num_rows)]

    def generate_random_location(self):
        x = random.randint(0, self.num_cols - 1)
        y = random.randint(0, self.num_rows - 1)
        return Location(x, y)

    def populate_agents(self, num_riders, num_drivers):
        self.riders = [Rider(self) for _ in range(num_riders)]
        self.drivers = [Driver(self) for _ in range(num_drivers)]

class Location:
    def __init__(self, x, y):
        self.x = x
        self.y = y

    def distance_to(self, other):
        #Manhattan distance (grid-based distance) between two locations
        distance_x = abs(self.x - other.x)
        distance_y = abs(self.y - other.y)
        return distance_x + distance_y


class Order:
    def __init__(self, pickup_location, dropoff_location):
        self.pickup_location = pickup_location
        self.dropoff_location = dropoff_location

class Agent:
    def __init__(self, city):
        self.city = city
        self.location = city.generate_random_location()

class Rider(Agent):
    def __init__(self, city, rider_status='offline'):
        super().__init__(city)
        self.rider_status = rider_status

    def request_ride(self, distance):
        destination = self.city.generate_random_location()
        distance = self.location.distance_to(destination)

class Route():
    def __init__(self, city, pickup_location, dropoff_location):
        self.city = city
        self.pickup_location = pickup_location
        self.dropoff_location = dropoff_location

    def distance(self):
        return self.pickup_location.distance_to(self.dropoff_location)


class Driver(Agent):
    def __init__(self, city, driver_status='offline'):
        super().__init__(city)
        self.driver_status = driver_status

def run_simulation():
    city = City(GRID_NUM_ROWS, GRID_NUM_COLS)
    city.populate_agents(NUM_OF_RIDERS, NUM_OF_DRIVERS)
    for tick in range(max_tick_count):
        print(f"Tick: {tick}, Riders: {len(city.riders)}, Drivers: {len(city.drivers)}")

class RiderSession:
    def __init__(self, rider):
        self.rider = rider
        self.active_order = None

    # 01. Rider searches for a ride: selects pickup (rider's current location as default) and dropoff (random location as default in V1) - requests a quote
    # 02. Quote should consist of: price, estimated time of arrival (ETA) - wait time for driver to arrive at pickup location, estimated time of arrival at dropoff location
    # 03. To calculate the quote, system needs to calculate the distance between pickup and dropoff locations, expected travel time, and also the distance between the rider and the nearest available driver
    # 04. System generates a quote based on the estimated distance and travel time (V1), other factors to include later (e.g., surge pricing, driver availability)
    # 05. Based on the quote, the rider can either accept or reject the ride request. 
    # 06. If rider accepts the ride request, the system generates an order. [Order is created when the rider accepts the quote, regardless of whether a driver accepts it. The order will be in a pending state until a driver accepts it. If no driver accepts the order within a certain time frame [to be defined], the order will be canceled and the rider will be notified.]
    # 07. The order is then sent to the nearest available driver. The driver can either accept or reject the order. If the driver accepts the order, the rider is notified and the driver proceeds to the pickup location. If the driver rejects the order (active rejection) or doesn't accept (after a time frame - to be defined), the system will send the order to the next nearest available driver until a driver accepts it or all drivers have rejected it. This loop continues x number of times (to be defined) until a driver accepts the order or all drivers have rejected it. If all drivers have rejected the order, the rider will be notified and the order will be canceled.
    # 08. Once a driver accepts the order, the driver proceeds to the pickup location. 
    # 09. The system will track the driver's location and update the rider on the driver's ETA to the pickup location. 
    # 10. Once the driver arrives at the pickup location, the rider is notified and can proceed to get in the vehicle. 
    # 11. WHen the rider is in the vehicle, the driver proceeds to the dropoff location.
    # The system will then track the driver's location and update the rider on the driver's ETA to the dropoff location. Once the driver arrives at the dropoff location, the rider is notified and can proceed to get out of the vehicle. The order is then marked as completed.


    def request_ride(self):
        if self.rider.rider_status == 'offline':
            print("Rider is offline and cannot request a ride.")
            return
        pickup_location = self.rider.location
        dropoff_location = self.rider.city.generate_random_location()
        self.active_order = Order(pickup_location, dropoff_location)
        print(f"Rider requested a ride from {pickup_location.x},{pickup_location.y} to {dropoff_location.x},{dropoff_location.y}")


run_simulation()