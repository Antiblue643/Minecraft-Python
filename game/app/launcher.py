import argparse

import pygame as pg

from game.app.game import Game


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--username",
        default=None,
        help="Minecraft username to load the player skin for",
    )
    args = parser.parse_args()

    game = Game(username=args.username)
    try:
        game.run()
    finally:
        if game.world is not None:
            world = game.world
            try:
                if game.current_screen is not None:
                    game.display_gui_screen(None)
            except Exception as exc:
                print(f"Warning: could not close the open screen: {exc}")
            try:
                if not game.save_world_with_progress(stop_workers=True):
                    print("Warning: world save did not complete successfully during shutdown.")
            except Exception as exc:
                print(f"Warning: unexpected error while saving during shutdown: {exc}")
            finally:
                for chunk in list(world.chunks.values()):
                    chunk.release()
                world.chunks.clear()
                if world.save_store is not None:
                    world.save_store.close(wait_for_completion=True)
        try:
            game._flush_gc_profile_events()
            game._set_gc_profile_enabled(False)
            game.performance_log.stop()
        except Exception as exc:
            print(f"Warning: could not close performance log: {exc}")
        try:
            gpu_profiler = getattr(game, "gpu_timer_profiler", None)
            if gpu_profiler is not None:
                gpu_profiler.close()
        except Exception as exc:
            print(f"Warning: could not release GPU timer queries: {exc}")
        pg.quit()


if __name__ == "__main__":
    main()
