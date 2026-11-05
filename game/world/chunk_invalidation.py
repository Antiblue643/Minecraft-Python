

def mark_chunk_boundary_dirty(chunk, edge):
    blocks = getattr(chunk, "blocks", None)
    if blocks is None:
        chunk.mark_all_dirty()
        return len(getattr(chunk, "dirty_sections", ()))

    width, height, depth = chunk.width, chunk.height, chunk.depth
    section_height = max(1, int(getattr(chunk, "section_height", 16)))
    section_count = int(getattr(
        chunk, "section_count", (height + section_height - 1) // section_height,
    ))
    air_ids = {0, int(getattr(chunk, "_block_type_to_id", {}).get("air", 0))}
    dirty = []
    for section_idx in range(section_count):
        y0 = section_idx * section_height
        y1 = min(height, y0 + section_height)
        found = False
        if edge == "west":
            bases = (((0 * depth + lz) * height) for lz in range(depth))
        elif edge == "east":
            lx = width - 1
            bases = (((lx * depth + lz) * height) for lz in range(depth))
        elif edge == "north":
            lz = 0
            bases = (((lx * depth + lz) * height) for lx in range(width))
        elif edge == "south":
            lz = depth - 1
            bases = (((lx * depth + lz) * height) for lx in range(width))
        else:
            raise ValueError(f"Unknown chunk edge {edge!r}")

        for base in bases:
            for y in range(y0, y1):
                if blocks[base + y] not in air_ids:
                    found = True
                    break
            if found:
                break
        if found:
            dirty.append(section_idx)

    mark_section = getattr(chunk, "_mark_section_dirty", None)
    for section_idx in dirty:
        if mark_section is not None:
            mark_section(section_idx)
        else:
            chunk.mark_dirty_at(section_idx * section_height)
    return len(dirty)


def invalidate_loaded_chunk_neighbors(chunks, cx, cz):
    neighbors = (
        ((cx - 1, cz), "east"),
        ((cx + 1, cz), "west"),
        ((cx, cz - 1), "south"),
        ((cx, cz + 1), "north"),
    )
    for neighbor_key, edge in neighbors:
        neighbor = chunks.get(neighbor_key)
        if neighbor is None:
            continue
        if neighbor._has_mesh or len(neighbor.dirty_sections) < neighbor.section_count:
            mark_chunk_boundary_dirty(neighbor, edge)
