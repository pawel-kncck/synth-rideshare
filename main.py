import random


def generate_drivers(count=10, seed=0):
    rng = random.Random(seed)
    return [{"id": int(f"9{id_}")} for id_ in rng.sample(range(1, 1_000_000_000), count)]


def generate_riders(count=100, seed=0):
    rng = random.Random(seed)
    return [{"id": int(f"8{id_}")} for id_ in rng.sample(range(1, 10_000_000_000), count)]


if __name__ == "__main__":
    print(generate_drivers())
    print(generate_riders())
