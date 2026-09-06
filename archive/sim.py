import simpy
import random

env = simpy.Environment()

class Rider(object):
    def __init__(self, env, name, location=[None, None]):
        self.env = env
        self.name = name
        self.location = [random.randint(0, 9), random.randint(0, 9)] if location == [None, None] else location

riders = [Rider(env, f"Rider {i}") for i in range(5)]  # Create 5 riders

class RiderSession(object):
    def __init__(self, env, rider: Rider = None):
        self.env = env
        self.rider = rider if rider is not None else riders.pop(random.randint(0, len(riders) - 1))  # Randomly select a rider for the session

    def rider_session(self, env):
        while True:
            print(f"{self.rider.name} session started at time {self.env.now}")
            yield self.env.timeout(10)  # Simulate the duration of the rider session
            print(f"{self.rider.name} session ended at time {self.env.now}")
            yield self.env.timeout(5)  # Simulate time between sessions


rider_session_1 = RiderSession(env)
env.process(rider_session_1.rider_session(env))
env.run(until=100)