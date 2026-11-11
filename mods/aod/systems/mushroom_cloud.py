
import math
import random


def spawn_mushroom_cloud(world, x, y, z, rng=random):
    spawn = getattr(world, "spawn_particle", None)
    if not callable(spawn):
        return 0

    x, y, z = float(x), float(y), float(z)
    emitted = 0

    for index in range(64):
        angle = (math.tau * index / 64.0) + rng.uniform(-0.08, 0.08)
        radius = rng.uniform(0.8, 3.8)
        px = x + math.cos(angle) * radius
        pz = z + math.sin(angle) * radius
        speed = rng.uniform(0.18, 0.72)
        spawn(
            "explode", px, y + rng.uniform(0.2, 1.8), pz,
            math.cos(angle) * speed, rng.uniform(0.08, 0.42),
            math.sin(angle) * speed,
        )
        emitted += 1

    for _ in range(84):
        height = rng.uniform(1.0, 9.5)
        width = 1.0 + height * 0.12
        angle = rng.uniform(0.0, math.tau)
        radius = math.sqrt(rng.random()) * width
        px = x + math.cos(angle) * radius
        pz = z + math.sin(angle) * radius
        py = y + height
        name = "largesmoke" if rng.random() < 0.42 else "smoke"
        spawn(name, px, py, pz)
        emitted += 1

    for _ in range(144):
        angle = rng.uniform(0.0, math.tau)
        radial = math.sqrt(rng.random()) * 7.5
        dome = 1.0 - (radial / 7.5) ** 2
        px = x + math.cos(angle) * radial
        pz = z + math.sin(angle) * radial
        py = y + 9.5 + dome * 3.2 + rng.uniform(-0.8, 0.8)
        name = "largesmoke" if rng.random() < 0.78 else "smoke"
        spawn(name, px, py, pz)
        emitted += 1

    for _ in range(48):
        height = rng.uniform(4.0, 13.0)
        angle = rng.uniform(0.0, math.tau)
        radius = rng.uniform(2.0, 7.0)
        spawn(
            "smoke",
            x + math.cos(angle) * radius,
            y + height,
            z + math.sin(angle) * radius,
        )
        emitted += 1

    return emitted
