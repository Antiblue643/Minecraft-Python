from __future__ import annotations

from datetime import datetime
from pathlib import Path


def save_screenshot(surface=None, *, directory=None, now=None, image_saver=None):
    if surface is None:
        import pygame as pg
        surface = pg.display.get_surface()
    if surface is None:
        return None

    if directory is None:
        from game.app.paths import user_data_root
        directory = user_data_root() / "screenshots"
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    timestamp = (now or datetime.now()).strftime("%Y-%m-%d_%H.%M.%S")
    path = directory / f"{timestamp}.png"
    suffix = 1
    while path.exists():
        path = directory / f"{timestamp}_{suffix}.png"
        suffix += 1

    if image_saver is None:
        import pygame as pg
        image_saver = pg.image.save
        try:
            from OpenGL.GL import (
                GL_FRONT, GL_PACK_ALIGNMENT, GL_RGBA, GL_UNSIGNED_BYTE,
                glReadBuffer, glPixelStorei, glReadPixels,
            )
            width, height = int(surface.get_width()), int(surface.get_height())
            glReadBuffer(GL_FRONT)
            glPixelStorei(GL_PACK_ALIGNMENT, 1)
            pixels = glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE)
            if pixels and any(pixels):
                image = pg.image.fromstring(bytes(pixels), (width, height), "RGBA", True)
                image_saver(image, str(path))
                return path
        except (ImportError, RuntimeError, ValueError, TypeError, pg.error):
            pass
    image_saver(surface, str(path))
    return path
