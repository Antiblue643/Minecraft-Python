
from __future__ import annotations

import math
from types import SimpleNamespace

from game.block.fire_rules import is_normal_cube
from game.block import Block


def _java_int(value):
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value & 0x80000000 else value


def _java_chunk_order(cells):
    size = 16
    while len(cells) > size * 0.75:
        size *= 2
    buckets = {}
    for cell in cells:  
        h = _java_int((cell[0] << 8) | cell[1]) & 0xFFFFFFFF
        h ^= (h >> 20) ^ (h >> 12)
        h ^= (h >> 7) ^ (h >> 4)
        buckets.setdefault(h & (size - 1), []).append(cell)
    ordered = []
    for index in sorted(buckets):
        ordered.extend(reversed(buckets[index]))
    return ordered


class NaturalMobSpawner:
    CHUNK_RADIUS = 4
    PASSES_PER_TICK = 3
    MIN_DISTANCE_SQ = 576.0  

    def __init__(
        self, max_spawns, entity_types, *, monsters=False, count_type=None,
        chunk_radius=None, passes_per_tick=None, chunk_chance=10,
        group_hops=2, uniform_spawn_height=False, scale_spawn_cap=False,
    ):
        self.max_spawns = int(max_spawns)
        self.count_type = count_type
        self.entity_types = tuple(entity_types)
        self.monsters = bool(monsters)
        self.chunk_radius = self.CHUNK_RADIUS if chunk_radius is None else max(0, int(chunk_radius))
        self.passes_per_tick = self.PASSES_PER_TICK if passes_per_tick is None else max(1, int(passes_per_tick))
        self.chunk_chance = max(1, int(chunk_chance))
        self.group_hops = max(1, int(group_hops))
        self.uniform_spawn_height = bool(uniform_spawn_height)
        self.scale_spawn_cap = bool(scale_spawn_cap)
        self._order_cache = {}

    @staticmethod
    def _next_int(rng, bound):
        next_int = getattr(rng, "next_int", None)
        return next_int(bound) if callable(next_int) else rng.randrange(bound)

    def _random_point(self, world, chunk_x, chunk_z, rng):
        x = chunk_x + self._next_int(rng, 16)
        if self.uniform_spawn_height:
            y = self._next_int(rng, 128)
        elif self.monsters:
            y = self._next_int(rng, self._next_int(rng, 120) + 8)
        else:
            y = self._next_int(rng, 128)
        z = chunk_z + self._next_int(rng, 16)
        return x, y, z

    @staticmethod
    def _is_normal_cube(world, x, y, z):
        block_type = world.get_block_type(x, y, z)
        return is_normal_cube(Block._block_defs.get(block_type))

    @staticmethod
    def _is_liquid(world, x, y, z):
        block_type = world.get_block_type(x, y, z)
        return block_type is not None and str(block_type).startswith(("water", "lava"))

    @staticmethod
    def _chunk_of(world, x, z):
        chunk_index = getattr(world, "_get_chunk_index", None)
        if callable(chunk_index):
            return chunk_index(x, z)
        return math.floor(x / 16.0), math.floor(z / 16.0)

    def _eligible_chunks(self, world, player):
        center = self._chunk_of(world, float(player.pos[0]), float(player.pos[2]))
        order = self._order_cache.get(center)
        if order is None:
            radius = self.chunk_radius
            cells = [
                (center[0] + dx, center[1] + dz)
                for dx in range(-radius, radius + 1)
                for dz in range(-radius, radius + 1)
            ]
            order = _java_chunk_order(cells)
            if len(self._order_cache) > 64:
                self._order_cache.clear()
            self._order_cache[center] = order
        return order

    @staticmethod
    def _spawn_point(world):
        return (
            float(getattr(world, "spawn_x", 0)),
            float(getattr(world, "spawn_y", 64)),
            float(getattr(world, "spawn_z", 0)),
        )

    def _perform_spawning(self, world, player, rng) -> int:
        spawned = 0
        spawn_x, spawn_y, spawn_z = self._spawn_point(world)
        for chunk_x, chunk_z in self._eligible_chunks(world, player):
            if not self._is_loaded(world, chunk_x * 16, chunk_z * 16):
                continue
            if self._next_int(rng, self.chunk_chance) != 0:
                continue
            entity_type = self.entity_types[self._next_int(rng, len(self.entity_types))]
            x, y, z = self._random_point(world, chunk_x * 16, chunk_z * 16, rng)
            if self._is_normal_cube(world, x, y, z):
                continue
            if world.get_block_type(x, y, z) is not None:
                continue

            spawned_in_chunk = 0
            group_limit = max(1, int(getattr(entity_type, "MAX_SPAWN_GROUP", 4)))
            group_limit_reached = False
            for _ in range(3):
                hx, hy, hz = x, y, z
                for _ in range(self.group_hops):
                    hx += self._next_int(rng, 6) - self._next_int(rng, 6)
                    hy += self._next_int(rng, 1) - self._next_int(rng, 1)
                    hz += self._next_int(rng, 6) - self._next_int(rng, 6)
                    if not self._is_normal_cube(world, hx, hy - 1, hz):
                        continue
                    if (self._is_normal_cube(world, hx, hy, hz)
                            or self._is_liquid(world, hx, hy, hz)
                            or self._is_normal_cube(world, hx, hy + 1, hz)):
                        continue
                    if not self._is_loaded(world, hx, hz):
                        continue
                    fx, fy, fz = float(hx), hy - 0.5, float(hz)
                    dx = fx - float(player.pos[0])
                    dy = fy - float(player.pos[1])
                    dz = fz - float(player.pos[2])
                    if dx * dx + dy * dy + dz * dz < self.MIN_DISTANCE_SQ:
                        continue
                    sx = hx + 0.5 - spawn_x
                    sy = hy - spawn_y
                    sz = hz + 0.5 - spawn_z
                    if sx * sx + sy * sy + sz * sz < self.MIN_DISTANCE_SQ:
                        continue

                    entity = entity_type((fx, fy, fz), yaw=0.0, rng=rng)
                    from game.entities.sheep import Sheep
                    if isinstance(entity, Sheep):
                        entity.set_fleece_color(Sheep.random_fleece_color(rng))
                    next_float = getattr(rng, "next_float", None)
                    yaw_random = next_float() if callable(next_float) else rng.random()
                    entity.yaw = float(yaw_random) * 360.0
                    if entity.get_can_spawn_here(fx, fy, fz):
                        spawned += 1
                        spawned_in_chunk += 1
                        world.spawn_entity(entity)
                        from game.entities.spider import Spider
                        from game.entities.skeleton import Skeleton
                        if isinstance(entity, Spider) and self._next_int(rng, 100) == 0:
                            skeleton = Skeleton((fx, fy, fz), yaw=entity.yaw, rng=rng)
                            skeleton.yaw = entity.yaw
                            world.spawn_entity(skeleton)
                            mount = getattr(entity, "mount_rider", None)
                            if callable(mount):
                                mount(skeleton)
                            else:
                                entity.rider = skeleton
                                skeleton.riding_entity = entity
                                position = getattr(entity, "_position_rider", None)
                                if callable(position):
                                    position()
                        if spawned_in_chunk >= group_limit:
                            group_limit_reached = True
                            break
                if group_limit_reached:
                    break
        return spawned

    @staticmethod
    def _is_loaded(world, x, z):
        chunk_index = getattr(world, "_get_chunk_index", None)
        chunks = getattr(world, "chunks", None)
        if not isinstance(chunks, dict) or not callable(chunk_index):
            return True
        chunk = chunks.get(chunk_index(x, z))
        if chunk is None:
            return False
        return bool(getattr(chunk, "light_ready", True))

    def update(self, world, rng):
        if bool(getattr(world, "_multiplayer_client_world", False)):
            return
        player = getattr(world, "player", None)
        if not self.entity_types:
            return

        players = [player] if player is not None else []
        for pos in getattr(world, "_mp_remote_player_positions", ()) or ():
            try:
                xyz = tuple(float(pos[index]) for index in range(3))
            except (IndexError, TypeError, ValueError, OverflowError):
                continue
            if not all(math.isfinite(value) for value in xyz):
                continue
            chunk = self._chunk_of(world, xyz[0], xyz[2])
            if any(self._chunk_of(world, float(p.pos[0]), float(p.pos[2])) == chunk for p in players):
                continue
            players.append(SimpleNamespace(pos=xyz))

        if not players:
            return

        count_type = self.count_type or self.entity_types[0].__mro__[1]
        for spawn_player in players:
            eligible = self._eligible_chunks(world, spawn_player)
            spawn_cap = self.max_spawns
            if self.scale_spawn_cap:
                spawn_cap = max(1, self.max_spawns * len(eligible) // 256)
            for _ in range(self.passes_per_tick):
                current = sum(
                    1 for entity in getattr(world, "entities", ())
                    if isinstance(entity, count_type)
                    and not getattr(entity, "dead", False)
                )
                if current >= spawn_cap:
                    return
                self._perform_spawning(world, spawn_player, rng)


class NaturalWaterMobSpawner:

    CHUNK_RADIUS = 8
    MAX_PER_256_CHUNKS = 5

    def __init__(self, entity_type):
        self.entity_type = entity_type
        self._chunk_helper = NaturalMobSpawner(
            1, (entity_type,), chunk_radius=self.CHUNK_RADIUS,
            passes_per_tick=1, chunk_chance=1,
        )

    @staticmethod
    def _opaque(world, x, y, z):
        block_type = world.get_block_type(x, y, z)
        if block_type is None:
            return False
        block = world.get_block(x, y, z)
        if block is None:
            return False
        if getattr(block, "opaque", None) is not None:
            return bool(block.opaque) and not bool(getattr(block, "intangible", False))
        return is_normal_cube(Block._block_defs.get(block_type))

    @staticmethod
    def _players(world):
        player = getattr(world, "player", None)
        players = [player] if player is not None else []
        known_positions = {tuple(float(value) for value in p.pos) for p in players}
        for pos in getattr(world, "_mp_remote_player_positions", ()) or ():
            try:
                xyz = tuple(float(pos[index]) for index in range(3))
            except (IndexError, TypeError, ValueError, OverflowError):
                continue
            if not all(math.isfinite(value) for value in xyz) or xyz in known_positions:
                continue
            known_positions.add(xyz)
            players.append(SimpleNamespace(pos=xyz))
        return players

    def _try_chunk(self, world, players, chunk_x, chunk_z, rng):
        if not NaturalMobSpawner._is_loaded(world, chunk_x * 16, chunk_z * 16):
            return 0
        next_int = NaturalMobSpawner._next_int
        x = chunk_x * 16 + next_int(rng, 16)
        y = next_int(rng, 128)
        z = chunk_z * 16 + next_int(rng, 16)
        if self._opaque(world, x, y, z):
            return 0
        if world.get_block_type(x, y, z) not in ("water", "flowing_water"):
            return 0

        spawned_in_group = 0
        spawn_players = players
        spawn_x = float(getattr(world, "spawn_x", 0.0))
        spawn_y = float(getattr(world, "spawn_y", 64.0))
        spawn_z = float(getattr(world, "spawn_z", 0.0))
        for _group in range(3):
            hx, hy, hz = x, y, z
            for _hop in range(4):
                hx += next_int(rng, 6) - next_int(rng, 6)
                hz += next_int(rng, 6) - next_int(rng, 6)
                if world.get_block_type(hx, hy, hz) not in ("water", "flowing_water"):
                    continue
                if self._opaque(world, hx, hy + 1, hz):
                    continue
                fx, fy, fz = float(hx) + 0.5, float(hy), float(hz) + 0.5
                too_close = False
                for other_player in spawn_players:
                    dx = fx - float(other_player.pos[0])
                    dy = fy - float(other_player.pos[1])
                    dz = fz - float(other_player.pos[2])
                    if dx * dx + dy * dy + dz * dz < 576.0:
                        too_close = True
                        break
                if too_close:
                    continue
                dx, dy, dz = fx - spawn_x, fy - spawn_y, fz - spawn_z
                if dx * dx + dy * dy + dz * dz < 576.0:
                    continue
                next_float = getattr(rng, "next_float", None)
                yaw = float(next_float()) * 360.0 if callable(next_float) else float(rng.random()) * 360.0
                entity = self.entity_type((fx, fy, fz), yaw=yaw, rng=rng)
                if not entity.get_can_spawn_here(fx, fy, fz):
                    continue
                world.spawn_entity(entity)
                spawned_in_group += 1
                if spawned_in_group >= int(getattr(entity, "MAX_SPAWN_GROUP", 4)):
                    return spawned_in_group
        return spawned_in_group

    def update(self, world, rng):
        if (bool(getattr(world, "debug_world", False))
                or bool(getattr(world, "is_hell_world", False))
                or bool(getattr(world, "_multiplayer_client_world", False))):
            return 0
        players = self._players(world)
        if not players:
            return 0

        eligible_set = set()
        for player in players:
            eligible_set.update(self._chunk_helper._eligible_chunks(world, player))
        eligible = _java_chunk_order(eligible_set)
        cap = self.MAX_PER_256_CHUNKS * len(eligible) // 256
        count = sum(
            1 for entity in getattr(world, "entities", ())
            if isinstance(entity, self.entity_type) and not getattr(entity, "dead", False)
        )
        if count > cap:
            return 0

        total_spawned = 0
        for cx, cz in eligible:
            total_spawned += self._try_chunk(world, players, cx, cz, rng)
        return total_spawned


_SPAWNERS = {}


def spawn_natural_mobs(world, rng):
    if bool(getattr(world, "debug_world", False)):
        return
    if bool(getattr(world, "is_hell_world", False)):
        spawners = _SPAWNERS.get("hell")
        if spawners is None:
            from game.entities.ghast import Ghast
            from game.entities.pigzombie import ZombiePigman
            from game.entities.mob import IMobs
            spawners = _SPAWNERS["hell"] = (
                NaturalMobSpawner(
                    100, (Ghast, ZombiePigman), monsters=True, count_type=IMobs,
                    chunk_radius=8, passes_per_tick=1, chunk_chance=50,
                    group_hops=4, uniform_spawn_height=True, scale_spawn_cap=True,
                ),
            )
        for spawner in spawners:
            spawner.update(world, rng)
        return
    spawners = _SPAWNERS.get("alpha")
    if spawners is None:
        from game.entities.creeper import Creeper
        from game.entities.pig import Pig
        from game.entities.sheep import Sheep
        from game.entities.cow import Cow
        from game.entities.chicken import Chicken
        from game.entities.skeleton import Skeleton
        from game.entities.spider import Spider
        from game.entities.zombie import Zombie
        from game.entities.slime import Slime
        from game.entities.animal import Animal
        from game.entities.mob import IMobs
        from game.entities.squid import Squid

        spawners = _SPAWNERS["alpha"] = (
            NaturalMobSpawner(
                200, (Zombie, Skeleton, Creeper, Spider, Slime), monsters=True,
                count_type=IMobs,
            ),
            NaturalMobSpawner(20, (Sheep, Pig, Cow, Chicken), count_type=Animal),
            NaturalWaterMobSpawner(Squid),
        )
    for spawner in spawners:
        spawner.update(world, rng)
