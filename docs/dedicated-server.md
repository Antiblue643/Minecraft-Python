# Dedicated server

The project includes a dedicated server. It uses the same world simulation and
multiplayer protocol as the in-game LAN host.

```powershell
# List saved worlds
python dedicated_server.py --list-worlds

# Host an existing saved world
python dedicated_server.py --world MyWorld --port 25565

# Create and host a new world
python dedicated_server.py --world FreshWorld --create --seed 12345
```

The server reads and writes worlds in the normal `saves` directory. When
attached to a terminal, it accepts server commands such as `/help`, `/save-all`,
and `/stop`; use `--no-console` to disable stdin commands. Autosaving defaults
to every five minutes and can be changed with `--autosave-interval` (use `0` to
disable it). Run `python dedicated_server.py --help` to see all options.

Dedicated hosting is experimental. Test it on a copy of a world before relying
on it. All clients and hosts must use the same updated build.

See [Gameplay and saves](gameplay.md) for save locations and [Getting started](getting-started.md) for requirements.
