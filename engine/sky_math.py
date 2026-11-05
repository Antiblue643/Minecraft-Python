
from __future__ import annotations

import math


def sunrise_color(celestial_angle: float):
    cosine = math.cos(float(celestial_angle) * math.pi * 2.0)
    if cosine < -0.4 or cosine > 0.4:
        return None

    blend = (cosine + 0.4) / 0.8
    alpha = 1.0 - (1.0 - math.sin(blend * math.pi)) * 0.99
    alpha *= alpha
    return (
        blend * 0.3 + 0.7,
        blend * blend * 0.7 + 0.2,
        0.2,
        alpha,
    )


def sunrise_fan_vertices(alpha: float, sectors: int = 16):
    sectors = max(3, int(sectors))
    alpha = max(0.0, min(1.0, float(alpha)))
    vertices = [(0.0, 100.0, 0.0)]
    for sector in range(sectors + 1):
        angle = sector * math.pi * 2.0 / sectors
        sine = math.sin(angle)
        cosine = math.cos(angle)
        vertices.append((
            sine * 120.0,
            cosine * 120.0,
            -cosine * 40.0 * alpha,
        ))
    return tuple(vertices)
