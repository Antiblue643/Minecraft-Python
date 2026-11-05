
from setuptools import Extension, setup

try:
    from Cython.Build import cythonize
except ImportError as exc:  
    raise SystemExit(
        "Cython is optional. Install it with `python -m pip install Cython`, "
        "then rerun setup.py."
    ) from exc

extensions = cythonize(
    [Extension("game.world.chunk", ["game/world/chunk.py"])],
    build_dir="build/cython",
    compiler_directives={
        "language_level": 3,
        "infer_types": True,
        "binding": True,
        "embedsignature": True,
        "boundscheck": True,
        "wraparound": True,
        "initializedcheck": True,
    },
    annotate=False,
)

setup(
    name="minecraft-python-chunk-acceleration",
    version="0.1.0",
    description="Optional Cython-compiled chunk mesher for Minecraft Python",
    ext_modules=extensions,
)
