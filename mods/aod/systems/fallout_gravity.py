
FALLOUT_VARIANT = "fallout"


def initialize():
    from game.block import support_rules
    from game.block import updates as block_updates
    from game.entities import falling_block

    gravity_blocks = frozenset(set(support_rules.GRAVITY_BLOCKS) | {FALLOUT_VARIANT})

    support_rules.GRAVITY_BLOCKS = gravity_blocks

    falling_block.GRAVITY_BLOCKS = gravity_blocks
    falling_block.TICK_HANDLERS[FALLOUT_VARIANT] = falling_block.try_to_fall

    block_updates.TICK_RATES[FALLOUT_VARIANT] = int(
        block_updates.TICK_RATES.get("sand", 3)
    )
    return gravity_blocks
