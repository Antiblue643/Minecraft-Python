
from pathlib import Path

from OpenGL.GL import GL_CLAMP, GL_CLAMP_TO_EDGE, GL_REPEAT

from engine.textures import Texture
from game.app.texturepacks import VANILLA_TEXTURE_ROOT


_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".gif"})
_PRELOADED_TEXTURES = []  
_REPEAT_MODES = {
    "terrain.png": GL_CLAMP,
    "title/mojang.png": GL_CLAMP,
    "title/black.png": GL_CLAMP,
    "item/arrows.png": GL_CLAMP_TO_EDGE,
    "misc/shadow.png": GL_CLAMP_TO_EDGE,
    "misc/water.png": GL_REPEAT,
}


def preload_texture_resources(texture_root=None):
    root = Path(texture_root) if texture_root is not None else VANILLA_TEXTURE_ROOT
    if not root.is_dir():
        if texture_root is None:
            raise FileNotFoundError(
                f"Required vanilla texture pack is missing: {root}"
            )
        return 0
    loaded = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _IMAGE_SUFFIXES:
            continue
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            relative = path.name
        texture = Texture(str(path), repeat_mode=_REPEAT_MODES.get(relative, GL_REPEAT))
        _PRELOADED_TEXTURES.append(texture)
        loaded += 1
    return loaded
