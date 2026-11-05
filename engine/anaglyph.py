
import numpy as np
import pygame as pg


def anaglyph_rgb(red, green, blue):
    return (
        (red * 30 + green * 59 + blue * 11) / 100,
        (red * 30 + green * 70) / 100,
        (red * 30 + blue * 70) / 100,
    )


def anaglyph_surface(surface):
    converted = surface.convert_alpha()
    pixels = pg.surfarray.pixels3d(converted)
    red, green, blue = (
        pixels[:, :, channel].astype(np.uint16) for channel in range(3)
    )
    pixels[:, :, 0] = (red * 30 + green * 59 + blue * 11) // 100
    pixels[:, :, 1] = (red * 30 + green * 70) // 100
    pixels[:, :, 2] = (red * 30 + blue * 70) // 100
    del pixels
    return converted
