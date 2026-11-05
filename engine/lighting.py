
from __future__ import annotations

SHADE_TOP = 1.00
SHADE_BOTTOM = 0.50
SHADE_NORTH_SOUTH = 0.80   
SHADE_EAST_WEST = 0.60     

MAX_LIGHT = 15

LIGHT_BRIGHTNESS_TABLE = (
    0.05,   
    0.067,
    0.085,
    0.106,
    0.129,
    0.156,
    0.186,
    0.221,
    0.261,
    0.309,
    0.367,
    0.439,
    0.529,
    0.645,
    0.799,
    1.000,  
)


def face_shade(direction):
    dx, dy, dz = direction
    if dy > 0:
        return SHADE_TOP
    if dy < 0:
        return SHADE_BOTTOM
    if dz != 0:
        return SHADE_NORTH_SOUTH
    if dx != 0:
        return SHADE_EAST_WEST
    return 1.0


def brightness_from_level(level, *, hell=False):
    t = max(0, min(int(level), MAX_LIGHT))
    if hell:
        ambient = 0.1
        darkness = 1.0 - (float(t) / 15.0)
        return ((1.0 - darkness) / (darkness * 3.0 + 1.0)
                * (1.0 - ambient) + ambient)
    return LIGHT_BRIGHTNESS_TABLE[t]


def face_color(direction, light_level=None, *, hell=False):
    shade = face_shade(direction)
    if light_level is not None:
        shade *= brightness_from_level(light_level, hell=hell)
    return (shade, shade, shade)


class Lighting:

    MAX_LIGHT = MAX_LIGHT
    SHADE_TOP = SHADE_TOP
    SHADE_BOTTOM = SHADE_BOTTOM
    SHADE_NORTH_SOUTH = SHADE_NORTH_SOUTH
    SHADE_EAST_WEST = SHADE_EAST_WEST

    def __init__(self):
        self.skylight_subtracted = 0

    def face_shade(self, direction):
        return face_shade(direction)

    def face_color(self, direction, light_level=None, *, hell=False):
        return face_color(direction, light_level, hell=hell)

    def effective_sky_light(self, sky_level):
        return max(0, int(sky_level) - self.skylight_subtracted)

    def combined_light(self, sky_level, block_level):
        return max(self.effective_sky_light(sky_level), int(block_level))


default_lighting = Lighting()
