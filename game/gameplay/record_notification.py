
import colorsys


def record_notification_style(remaining_seconds):
    remaining_ticks = max(0.0, float(remaining_seconds)) * 20.0
    alpha_byte = min(255, int(remaining_ticks * 256.0 / 20.0))
    if alpha_byte <= 0:
        return None

    hue = (remaining_ticks / 50.0) % 1.0
    rgb_float = colorsys.hsv_to_rgb(hue, 0.7, 0.6)
    rgb = tuple(int(channel * 255.0 + 0.5) for channel in rgb_float)
    return rgb, alpha_byte / 255.0


def record_notification_layout(display_width, display_height, logical_text_width,
                               gui_scale=3):
    scale = max(1, int(gui_scale))
    scaled_width = (int(display_width) + scale - 1) // scale
    scaled_height = (int(display_height) + scale - 1) // scale
    center_x = (scaled_width // 2) * scale
    x = center_x - (int(logical_text_width) // 2) * scale
    y = (scaled_height - 48 - 4) * scale
    return x, y, scale
