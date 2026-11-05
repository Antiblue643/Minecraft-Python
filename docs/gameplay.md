# Gameplay and saves

## Default controls

| Action                               | Control             |
|--------------------------------------|---------------------|
| Move                                 | W, A, S, D          |
| Jump                                 | Space               |
| Break blocks / attack                | Left mouse button   |
| Pick block                           | Middle mouse button |
| Place blocks / use items             | Right mouse button  |
| Select hotbar slot                   | 1–9 or mouse wheel  |
| Open inventory                       | E                   |
| Drop held item                       | Q                   |
| Open game menu                       | Esc                 |
| Toggle render distance               | F                   |
| Hide HUD / front-facing third person | F1                  |
| Toggle debug overlay + lagometer     | F3                  |
| Toggle third person                  | F5                  |
| Start performance recording          | F8                  |

Control rebinding is keyboard/mouse-based. Other gameplay and graphics options
are available in **Options**; when mods expose settings, **Mod settings...** is added.

## Data and saves

When running from source, settings and saves are stored in the project directory.
In a packaged Windows build, user data is stored under
`%LOCALAPPDATA%\.minecraftpy`. Worlds are saved in the `saves` folder; keep a
backup of worlds you want to preserve. To create a debug world, hold **Alt**
while selecting an empty world slot.

See [Dedicated server](dedicated-server.md) for instructions on hosting a world.
