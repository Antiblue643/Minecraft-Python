

def gui_scale(width, height):
    width = max(1, int(width))
    height = max(1, int(height))
    scale = 1
    while (
        width // (scale + 1) >= 320
        and height // (scale + 1) >= 240
    ):
        scale += 1
    return scale


def scaled_dimensions(width, height):
    width = max(1, int(width))
    height = max(1, int(height))
    scale = gui_scale(width, height)
    return scale, (width + scale - 1) // scale, (height + scale - 1) // scale


def to_gui_coordinates(x, y, scale):
    scale = max(1, int(scale))
    return int(int(x) / scale), int(int(y) / scale)
