# Mods

## Built-in mods

The project includes six bundled mods. They can be enabled or disabled from
**Mods & Texture Packs**; changes take effect after restarting the game.

| Mod                    | Description                                                                     |
|------------------------|---------------------------------------------------------------------------------|
| **Corruptor Arrows**   | Adds craftable arrows that scramble nearby blocks when they hit a surface.      |
| **Controller Support** | Adds gamepad input and controller navigation for menus.                         |
| **Creative Mode**      | Adds Beta 1.8-like creative mode, plus some extra debug tools.                  |
| **Eval**               | Adds a restricted expression evaluator to chat through `/eval` and `eval(...)`. |
| **Herobrine**          | Adds... well... you know... (singleplayer)                                      |
| **Skin Fix**           | Adds modern 64×64 player skins, including overlays and slim-arm support.        |

## External Python mods

External mods go in the `mods` folder (the **Open mods folder** button opens the
active folder). Each mod must have its own folder, a `mod.json` manifest, and
an entry-point Python file:

```text
mods/
  example/
    mod.json
    mod.py
    helper.py
    lang/
      en_US.lang   # required for bundled mods; recommended for external mods
      fr_FR.lang   # optional additional locale
    pack.png       # optional 32x32 icon
```

Example `mod.json`:

```json
{
  "id": "example",
  "name": "Example Mod",
  "version": "1.0",
  "description": "An example external mod",
  "entry": "mod.py",
  "default_enabled": false
}
```

`default_enabled` is optional and defaults to `false`.

When enabled, the entry file is imported once at game startup. It may define
`initialize(game)` to run setup against the active game instance; for example:

```python
def initialize(game):
    print("Example mod loaded")
```

Mods must keep their code and assets inside their own folder. They may attach
wrappers to existing game classes and functions at startup, but must not edit
core source files or add new modules under `game/` or `engine/`. The current
explicit gameplay hook is `on_world_tick(game, world, player, dt)`, called after
each world update. Other wrapper targets are internal implementation details
and may change between releases. Mods can add chat commands with
`register_command(name, handler)` and rewrite command text with
`register_preprocessor(key, func)` from `game.multiplayer.commands` (see the
bundled `eval` mod, which provides `/eval` and `eval(...)` arguments). Enabling
or disabling an external mod takes effect after restarting the game. Python
mods execute with the same permissions as the game; install only mods from
sources you trust.

Each mod can store its own JSON settings by using
`load_mod_config(identifier, defaults)` and `save_mod_config(identifier, config)`
from `game.app.mods`. The loader stores these under
`mods/_configs/<identifier>/config.json` in the user-data folder; the mod
selection screen stores each mod's enabled state in that same per-mod config.

See [Getting started](getting-started.md) to install and launch the game.


## Mod language files

A mod owns its strings in its own language directory. Add one Java-properties-
style file per locale:

```text
mods/my_mod/lang/en_US.lang
mods/my_mod/lang/fr_FR.lang
```

Use `translate_mod` from the recreation's localization API to read a string
from that mod's namespace:

```python
from game.localization import translate_mod

label = translate_mod("my_mod", "screen.title", default="My Screen")
```

Recommended keys include `mod.name`, `mod.description`, `settings.<key>`,
`screen.<name>`, `item.<item_id>.name`, `item.<item_id>.desc`, and
`tile.<block_id>.name`. Every bundled mod has an `en_US.lang` file with its
name, description, and all declared settings labels. External mods should do
the same; manifest `name` and `description` are only fallback text if the
mod's language file is missing. Mod item/block definitions registered during
`initialize(game)` are automatically tagged with the owning mod ID, so their
inventory tooltips are looked up in that mod's language file.