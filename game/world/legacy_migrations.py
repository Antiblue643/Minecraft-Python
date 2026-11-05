from __future__ import annotations


def migrate_legacy_redstone_wire(raw_blocks, *, height=128, supporting_block_ids=()):
    raw = bytes(raw_blocks)
    migrated = bytearray(raw)
    support_ids = frozenset(int(value) & 0xFF for value in supporting_block_ids)
    wire_indexes = set()
    popped_indexes = set()

    for index, block_id in enumerate(raw):
        if block_id != 55:
            continue
        local_y = index % int(height)
        below_id = raw[index - 1] if local_y > 0 else 0
        if local_y > 0 and below_id in support_ids:
            wire_indexes.add(index)
        else:
            migrated[index] = 0
            popped_indexes.add(index)

    return bytes(migrated), frozenset(wire_indexes), frozenset(popped_indexes)
