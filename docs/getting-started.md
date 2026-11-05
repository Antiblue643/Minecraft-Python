# Getting started

## Requirements

- Python with `pip`
- NumPy and Numba
- A graphics environment with OpenGL support

The project uses Pygame CE, PyOpenGL, and `nbtlib` for world saves. The Cython chunk extension is optional and is not required to run the game.

## Run from source

From the project directory, install the dependencies and start the game:

```powershell
python -m pip install -r requirements.txt
python client.py
```

You can optionally set the player name when launching:

```powershell
python client.py --username Player
```

See [Gameplay and saves](gameplay.md) for controls and data locations, or [Mods](mods.md) to learn about mod support.
