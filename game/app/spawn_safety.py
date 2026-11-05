def safe_spawn_position(player, world, position, search_radius=8):
    origin = tuple(float(v) for v in position[:3])
    if player is None or world is None or not hasattr(player, "_collides"):
        return origin

    old_size = getattr(player, "size", (0.6, 1.8, 0.6))
    player.size = (0.6, 1.8, 0.6)

    def ensure_chunks_loaded(extent):
        get_chunk_index = getattr(world, "_get_chunk_index", None)
        generate_chunk = getattr(world, "_generate_chunk", None)
        chunks = getattr(world, "chunks", None)
        if not callable(get_chunk_index) or not callable(generate_chunk) or chunks is None:
            return
        ox, _oy, oz = origin
        try:
            min_cx, min_cz = get_chunk_index(ox - extent, oz - extent)
            max_cx, max_cz = get_chunk_index(ox + extent, oz + extent)
            for cx in range(min_cx, max_cx + 1):
                for cz in range(min_cz, max_cz + 1):
                    if (cx, cz) not in chunks:
                        generate_chunk(cx, cz)
        except Exception:
            pass

    try:
        ensure_chunks_loaded(1.0)
        try:
            if not player._collides(*origin):
                return origin
        except Exception:
            return origin

        ox, oy, oz = origin
        ensure_chunks_loaded(max(1, int(search_radius)) + 1)
        base_x, base_z = int(round(ox)), int(round(oz))
        offsets = []
        for radius in range(1, max(1, int(search_radius)) + 1):
            ring = [
                (dx, dz)
                for dx in range(-radius, radius + 1)
                for dz in range(-radius, radius + 1)
                if max(abs(dx), abs(dz)) == radius
            ]
            ring.sort(key=lambda offset: (
                offset[0] * offset[0] + offset[1] * offset[1],
                offset[0], offset[1],
            ))
            offsets.extend(ring)

        for dx, dz in offsets:
            x, z = base_x + dx, base_z + dz
            try:
                surface_y = int(world.terrain.height_at(x, z))
                floor_type = world.get_block_type(x, surface_y, z)
                if floor_type in (None, "water", "flowing_water", "lava", "flowing_lava"):
                    continue
                floor_block = world.get_block(x, surface_y, z)
                if floor_block is None or getattr(floor_block, "collision", True) is False:
                    continue
                candidate = (float(x), float(surface_y) + 0.5, float(z))
                if not player._collides(*candidate):
                    return candidate
            except Exception:
                continue

        for step in range(1, 17):
            candidate = (ox, oy + float(step), oz)
            try:
                if not player._collides(*candidate):
                    return candidate
            except Exception:
                break
        return origin
    finally:
        player.size = old_size
