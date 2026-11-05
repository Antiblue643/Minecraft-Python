from __future__ import annotations


def find_sand_spawn_candidate(terrain, search_radius: int = 128,
                              scan_start_y: int = 63):
    radius_limit = max(0, int(search_radius))
    for radius in range(radius_limit + 1):
        for x in range(-radius, radius + 1):
            for z in range(-radius, radius + 1):
                if max(abs(x), abs(z)) != radius:
                    continue
                biome = terrain.biomes.get_biome(x, z)
                if getattr(biome, "top_block", None) != "sand":
                    continue
                surface_y = int(terrain.height_at(x, z))
                if surface_y < int(scan_start_y):
                    continue
                return (float(x), float(surface_y) + 0.5, float(z))
    return None


def find_sand_spawn_surface(world, x: int, z: int, height: int = 128):
    surface_y = 63
    max_y = max(64, int(height)) - 1
    get_block = world.get_block_type
    while surface_y < max_y and get_block(int(x), surface_y + 1, int(z)) is not None:
        surface_y += 1
    return surface_y if get_block(int(x), surface_y, int(z)) == "sand" else None
