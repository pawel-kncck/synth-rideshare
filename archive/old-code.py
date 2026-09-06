import pyglet
from pyglet import shapes
import random

TICK = 1/60
tick_count = 0

SQUARE_SIZE = 30
GRID_NUM_ROWS = 30
GRID_NUM_COLS = 50 
NUM_OF_RIDERS = 10

window = pyglet.window.Window()
batch = pyglet.graphics.Batch()

GRID_CONTAINER_DIMENSIONS = (SQUARE_SIZE * GRID_NUM_COLS, SQUARE_SIZE * GRID_NUM_ROWS)
GRID_CONTAINER_ANCHOR = (window.width * 0.5, window.height * 0.5)

box = shapes.Box(
    GRID_CONTAINER_ANCHOR[0] - GRID_CONTAINER_DIMENSIONS[0] / 2,
    GRID_CONTAINER_ANCHOR[1] - GRID_CONTAINER_DIMENSIONS[1] / 2,
    GRID_CONTAINER_DIMENSIONS[0],
    GRID_CONTAINER_DIMENSIONS[1],
        color=(55, 55, 55),
    batch=batch
)

vertical_lines = []

for i in range(1, GRID_NUM_COLS):
    x = GRID_CONTAINER_ANCHOR[0] - GRID_CONTAINER_DIMENSIONS[0] / 2 + (GRID_CONTAINER_DIMENSIONS[0] / GRID_NUM_COLS) * i
    vertical_line = shapes.Line(
        x, GRID_CONTAINER_ANCHOR[1] - GRID_CONTAINER_DIMENSIONS[1] / 2,
        x, GRID_CONTAINER_ANCHOR[1] + GRID_CONTAINER_DIMENSIONS[1] / 2,
        color=(55, 55, 55),
        batch=batch
    )
    vertical_lines.append(vertical_line)

horizontal_lines = []

for i in range(1, GRID_NUM_ROWS):
    y = GRID_CONTAINER_ANCHOR[1] - GRID_CONTAINER_DIMENSIONS[1] / 2 + (GRID_CONTAINER_DIMENSIONS[1] / GRID_NUM_ROWS) * i
    horizontal_line = shapes.Line(
        GRID_CONTAINER_ANCHOR[0] - GRID_CONTAINER_DIMENSIONS[0] / 2, y,
        GRID_CONTAINER_ANCHOR[0] + GRID_CONTAINER_DIMENSIONS[0] / 2, y,
        color=(55, 55, 55),
        batch=batch
    )
    horizontal_lines.append(horizontal_line)

class Rider(shapes.Circle):
    def __init__(self, x=None, y=None, radius=SQUARE_SIZE / 4, color=None, batch=None):
        self.base_color = (75, 142, 250)
        self.light_color = (154, 192, 250)
        self.pulsing_frequency = 1  # cycles per second
        self.elapsed = 0.0          # accumulated time, driven by pulse(dt)
        self.speed = SQUARE_SIZE    # one grid cell per move step

        # Grid bounds (bottom-left corner and extent), matching the drawn box
        self.grid_left = GRID_CONTAINER_ANCHOR[0] - GRID_CONTAINER_DIMENSIONS[0] / 2
        self.grid_bottom = GRID_CONTAINER_ANCHOR[1] - GRID_CONTAINER_DIMENSIONS[1] / 2
        self.grid_right = self.grid_left + GRID_CONTAINER_DIMENSIONS[0]
        self.grid_top = self.grid_bottom + GRID_CONTAINER_DIMENSIONS[1]

        # Default to a random cell centre when no position is given
        if x is None:
            x = self.grid_left + random.randint(0, GRID_NUM_COLS - 1) * SQUARE_SIZE + SQUARE_SIZE
        if y is None:
            y = self.grid_bottom + random.randint(0, GRID_NUM_ROWS - 1) * SQUARE_SIZE + SQUARE_SIZE

        super().__init__(x, y, radius, color=color or self.base_color, batch=batch)

    def pulse(self, dt):
        import math

        # dt is the time since the last frame; accumulate it to get wall time
        self.elapsed += dt
        # 0..1 blend factor oscillating at pulsing_frequency
        t = (1 + math.sin(2 * math.pi * self.pulsing_frequency * self.elapsed)) / 2
        self.color = tuple(
            int(base + (light - base) * t)
            for base, light in zip(self.base_color, self.light_color)
        )

    def move(self, dx, dy):
        new_x = self.x + dx * self.speed
        new_y = self.y + dy * self.speed

        # Ensure the rider stays within the grid boundaries
        if (self.grid_left <= new_x <= self.grid_right
                and self.grid_bottom <= new_y <= self.grid_top):
            self.x = new_x
            self.y = new_y


riders = [Rider(batch=batch) for _ in range(NUM_OF_RIDERS)]


@window.event
def on_draw():
    window.clear()
    batch.draw()

pyglet.clock.schedule_interval(lambda dt: [rider.pulse(dt) for rider in riders], 1 / 60)

pyglet.app.run()
