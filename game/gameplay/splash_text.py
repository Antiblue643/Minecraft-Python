
import random
from datetime import date
from pathlib import Path


def _load_splashes():
    from game.app.texturepacks import resolve_texture_path

    try:
        path = Path(resolve_texture_path("game/textures/title/splashes.txt"))
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return ("missingno!",)
    splashes = tuple(line.strip() for line in lines if line.strip())
    return splashes or ("missingno!",)


def select_splash_text(today=None, username="", *, chooser=random.choice):
    today = today or date.today()
    if today.month == 11 and today.day == 9:
        return "Happy birthday, ez!"
    if today.month == 6 and today.day == 1:
        return "Happy birthday, Notch!"
    if today.month == 12 and today.day == 24:
        return "Merry X-mas!"
    if today.month == 1 and today.day == 1:
        return "Happy new year!"
    if today.month == 12 and today.day == 19:
        return "Happy birthday, Antiblue643!"
    return chooser(_load_splashes())
