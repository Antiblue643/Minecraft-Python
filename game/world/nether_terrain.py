from __future__ import annotations

import math
import random

from game.world.terrain import _Octaves
from game.world.java_random import JavaRandom
from game.world.biomes import BIOMES

CHUNK_SIZE = 16
WORLD_HEIGHT = 128
HELL_SEA_LEVEL = 32


class HellBiomeProvider:
    def get_biome(self, x: int, z: int):
        return BIOMES["hell"]

    def get_climate(self, x: int, z: int):
        return (1.0, 0.0)

    def get_raw_temperature(self, x: int, z: int):
        return 1.0

    def fill_climate_region(self, x: int, z: int, width: int, depth: int):
        count = max(0, int(width) * int(depth))
        return ([1.0] * count, [0.0] * count)

    def get_grass_color(self, x: int, z: int):
        return (0.45, 0.12, 0.08)

    def get_foliage_color(self, x: int, z: int):
        return (0.45, 0.12, 0.08)

    def get_grass_tint(self, x: int, z: int):
        return (115, 31, 20)

    def get_foliage_tint(self, x: int, z: int):
        return (115, 31, 20)


class NetherTerrainGenerator:
    def __init__(self, seed: int, world_height: int = WORLD_HEIGHT):
        if int(world_height) != WORLD_HEIGHT:
            raise ValueError("Alpha Nether chunks are exactly 128 blocks high")
        self.seed = int(seed)
        self.world_height = WORLD_HEIGHT
        self.sea_level = HELL_SEA_LEVEL
        self.snow_covered = False
        self.biomes = HellBiomeProvider()
        rng = JavaRandom(self.seed)
        self.noise_gen1 = _Octaves(rng, 16)
        self.noise_gen2 = _Octaves(rng, 16)
        self.noise_gen3 = _Octaves(rng, 8)
        self.noise_gen4 = _Octaves(rng, 4)
        self.noise_gen5 = _Octaves(rng, 4)
        self.noise_gen6 = _Octaves(rng, 10)
        self.noise_gen7 = _Octaves(rng, 16)
        self.rand = rng

    def _density_field(self, origin_x: int, origin_z: int):
        sx, sy, sz = 5, 17, 5
        shape = self.noise_gen6.generate(origin_x, 0, origin_z, sx, 1, sz, 1.0, 0.0, 1.0)
        limit = self.noise_gen7.generate(origin_x, 0, origin_z, sx, 1, sz, 100.0, 0.0, 100.0)
        main = self.noise_gen3.generate(
            origin_x, 0, origin_z, sx, sy, sz,
            684.412 / 80.0, 2053.236 / 60.0, 684.412 / 80.0,
        )
        low = self.noise_gen1.generate(
            origin_x, 0, origin_z, sx, sy, sz, 684.412, 2053.236, 684.412,
        )
        high = self.noise_gen2.generate(
            origin_x, 0, origin_z, sx, sy, sz, 684.412, 2053.236, 684.412,
        )
        result = [0.0] * (sx * sy * sz)
        y_falloff = []
        for iy in range(sy):
            value = math.cos(iy * math.pi * 6.0 / sy) * 2.0
            edge_y = float(iy if iy <= sy // 2 else sy - 1 - iy)
            if edge_y < 4.0:
                edge = 4.0 - edge_y
                value -= edge * edge * edge * 10.0
            y_falloff.append(value)
        index = 0
        nindex = 0
        for ix in range(sx):
            for iz in range(sz):
                shape_value = min(1.0, (shape[nindex] + 256.0) / 512.0)
                limit_value = abs(limit[nindex] / 8000.0) * 3.0 - 3.0
                if limit_value < 0.0:
                    limit_value /= 2.0
                    limit_value = max(-1.0, limit_value)
                    limit_value /= 1.4
                    limit_value /= 2.0
                    shape_value = 0.0
                else:
                    limit_value = min(1.0, limit_value) / 6.0
                shape_value += 0.5
                limit_value *= sy / 16.0
                nindex += 1
                for iy in range(sy):
                    blend = (main[index] / 10.0 + 1.0) / 2.0
                    if blend < 0.0:
                        density = low[index] / 512.0
                    elif blend > 1.0:
                        density = high[index] / 512.0
                    else:
                        density = (low[index] / 512.0) + ((high[index] - low[index]) / 512.0) * blend
                    density -= y_falloff[iy]
                    if iy > sy - 4:
                        fade = (iy - (sy - 4)) / 3.0
                        density = density * (1.0 - fade) + -10.0 * fade
                    if iy < limit_value:
                        fade = max(0.0, min(1.0, (limit_value - iy) / 4.0))
                        density = density * (1.0 - fade) + -10.0 * fade
                    result[index] = density
                    index += 1
        return result

    @staticmethod
    def _block_id(block_ids, name):
        try:
            return int(block_ids[name])
        except KeyError as exc:
            raise RuntimeError(f"Nether generation requires block {name!r}") from exc

    def generate_chunk_data(self, cx: int, cz: int, width: int, depth: int,
                            block_type_to_id: dict[str, int]) -> bytearray:
        if width != 16 or depth != 16:
            raise ValueError("Nether terrain generation requires 16x16 chunks")
        netherrack = self._block_id(block_type_to_id, "netherrack")
        lava = self._block_id(block_type_to_id, "lava")
        bedrock = self._block_id(block_type_to_id, "bedrock")
        gravel = self._block_id(block_type_to_id, "gravel")
        soul_sand = self._block_id(block_type_to_id, "soul_sand")
        data = bytearray(16 * 16 * 128)
        self.rand.set_seed(cx * 341873128712 + cz * 132897987541)
        field = self._density_field(cx * 4, cz * 4)

        for gx in range(4):
            for gz in range(4):
                base = ((gx * 5 + gz) * 17)
                base_z = ((gx * 5 + gz + 1) * 17)
                next_x = (((gx + 1) * 5 + gz) * 17)
                next_xz = (((gx + 1) * 5 + gz + 1) * 17)
                for gy in range(16):
                    d00, d01 = field[base + gy], field[base_z + gy]
                    d10, d11 = field[next_x + gy], field[next_xz + gy]
                    dy00 = (field[base + gy + 1] - d00) * 0.125
                    dy01 = (field[base_z + gy + 1] - d01) * 0.125
                    dy10 = (field[next_x + gy + 1] - d10) * 0.125
                    dy11 = (field[next_xz + gy + 1] - d11) * 0.125
                    for sy in range(8):
                        cur00, cur01 = d00, d01
                        step_x00 = (d10 - d00) * 0.25
                        step_x01 = (d11 - d01) * 0.25
                        y = gy * 8 + sy
                        for sx in range(4):
                            density = cur00
                            step_z = (cur01 - cur00) * 0.25
                            x = gx * 4 + sx
                            for sz in range(4):
                                z = gz * 4 + sz
                                block = lava if y < 32 else 0
                                if density > 0.0:
                                    block = netherrack
                                data[(x * 16 + z) * 128 + y] = block & 255
                                density += step_z
                            cur00 += step_x00
                            cur01 += step_x01
                        d00 += dy00
                        d01 += dy01
                        d10 += dy10
                        d11 += dy11

        rng = self.rand
        scale = 1.0 / 32.0
        noise_surface = self.noise_gen4.generate(cx * 16, cz * 16, 0.0, 16, 16, 1, scale, scale, 1.0)
        noise_gravel = self.noise_gen4.generate(cz * 16, 109.0134, cx * 16, 16, 1, 16, scale, 1.0, scale)
        noise_soul = self.noise_gen5.generate(cx * 16, cz * 16, 0.0, 16, 16, 1, scale * 2.0, scale * 2.0, scale * 2.0)
        for x in range(16):
            for z in range(16):
                pos = x + z * 16
                noise_pos = z * 16 + x  
                soul_flag = noise_surface[noise_pos] + rng.next_double() * 0.2 > 0.0
                gravel_flag = noise_gravel[noise_pos] + rng.next_double() * 0.2 > 0.0
                thickness = int(noise_soul[noise_pos] / 3.0 + 3.0 + rng.next_double() * 0.25)
                depth_remaining = -1
                top = netherrack
                filler = netherrack
                for y in range(127, -1, -1):
                    index = (x * 16 + z) * 128 + y
                    if y >= 127 - rng.next_int(5) or y <= rng.next_int(5):
                        data[index] = bedrock
                        continue
                    block = data[index]
                    if block == 0:
                        depth_remaining = -1
                    elif block == netherrack:
                        if depth_remaining == -1:
                            if thickness <= 0:
                                top, filler = 0, netherrack
                            elif 60 <= y <= 65:
                                top = filler = netherrack
                                if gravel_flag:
                                    top, filler = gravel, netherrack
                                if soul_flag:
                                    top = filler = soul_sand
                            if y < 64 and top == 0:
                                top = lava
                            depth_remaining = thickness
                            data[index] = top if y >= 63 else filler
                        elif depth_remaining > 0:
                            depth_remaining -= 1
                            data[index] = filler
        self._carve_hell_caves(cx, cz, data, netherrack, lava)
        return data

    def _carve_hell_caves(self, cx, cz, data, netherrack, lava):
        region_rng = JavaRandom(self.seed)
        def java_odd(value):
            half = value // 2 if value >= 0 else -((-value) // 2)
            return half * 2 + 1

        odd_x = java_odd(region_rng.next_long())
        odd_z = java_odd(region_rng.next_long())
        for source_x in range(cx - 8, cx + 9):
            for source_z in range(cz - 8, cz + 9):
                source_seed = (source_x * odd_x + source_z * odd_z) ^ self.seed
                rng = JavaRandom(source_seed)
                cave_count = rng.next_int(rng.next_int(rng.next_int(10) + 1) + 1)
                if rng.next_int(5) != 0:
                    cave_count = 0
                for _ in range(cave_count):
                    x = float(source_x * 16 + rng.next_int(16))
                    y = float(rng.next_int(128))
                    z = float(source_z * 16 + rng.next_int(16))
                    branches = 1
                    if rng.next_int(4) == 0:
                        self._carve_hell_cave_node(
                            cx, cz, data, netherrack, lava,
                            x, y, z, 1.0 + rng.next_float() * 6.0,
                            0.0, 0.0, -1, -1, 0.5, rng,
                        )
                        branches += rng.next_int(4)
                    for _branch in range(branches):
                        yaw = rng.next_float() * math.pi * 2.0
                        pitch = (rng.next_float() - 0.5) * 2.0 / 8.0
                        width = rng.next_float() * 2.0 + rng.next_float()
                        self._carve_hell_cave_node(
                            cx, cz, data, netherrack, lava,
                            x, y, z, width * 2.0, yaw, pitch,
                            0, 0, 0.5, rng,
                        )

    @staticmethod
    def _carve_hell_cave_node(cx, cz, data, netherrack, lava, x, y, z, width, yaw, pitch,
                              start_step, end_step, vertical_scale, source_rng):
        rng = JavaRandom(source_rng.next_long())
        center_x, center_z = cx * 16 + 8.0, cz * 16 + 8.0
        if end_step <= 0:
            end_step = 112 - rng.next_int(28)
        is_large = start_step == -1
        if is_large:
            start_step = end_step // 2
        split_step = rng.next_int(end_step // 2) + end_step // 4
        gentle = rng.next_int(6) == 0
        yaw_delta = pitch_delta = 0.0
        step = start_step
        while step < end_step:
            radius = 1.5 + math.sin(step * math.pi / end_step) * width
            vertical_radius = radius * vertical_scale
            cos_pitch, sin_pitch = math.cos(pitch), math.sin(pitch)
            x += math.cos(yaw) * cos_pitch
            y += sin_pitch
            z += math.sin(yaw) * cos_pitch
            pitch = pitch * (0.92 if gentle else 0.7) + pitch_delta * 0.1
            yaw += yaw_delta * 0.1
            pitch_delta *= 0.9
            yaw_delta *= 12.0 / 16.0
            pitch_delta += (rng.next_float() - rng.next_float()) * rng.next_float() * 2.0
            yaw_delta += (rng.next_float() - rng.next_float()) * rng.next_float() * 4.0
            if not is_large and step == split_step and width > 1.0:
                child_pitch = pitch / 3.0
                child1 = 0.5 + rng.next_float() * 0.5
                NetherTerrainGenerator._carve_hell_cave_node(
                    cx, cz, data, netherrack, lava, x, y, z, child1,
                    yaw - math.pi / 2.0, child_pitch, step, end_step, 1.0, source_rng,
                )
                child2 = 0.5 + rng.next_float() * 0.5
                NetherTerrainGenerator._carve_hell_cave_node(
                    cx, cz, data, netherrack, lava, x, y, z, child2,
                    yaw + math.pi / 2.0, child_pitch, step, end_step, 1.0, source_rng,
                )
                return
            if is_large or rng.next_int(4) != 0:
                dx, dz = x - center_x, z - center_z
                remaining = float(end_step - step)
                width_pad = width + 18.0
                if dx * dx + dz * dz - remaining * remaining > width_pad * width_pad:
                    return
                if not (center_x - 16.0 - radius * 2.0 <= x <= center_x + 16.0 + radius * 2.0
                        and center_z - 16.0 - radius * 2.0 <= z <= center_z + 16.0 + radius * 2.0):
                    step += 1
                    continue
                x0 = max(0, math.floor(x - radius) - cx * 16 - 1)
                x1 = min(16, math.floor(x + radius) - cx * 16 + 1)
                y0 = max(1, math.floor(y - vertical_radius) - 1)
                y1 = min(120, math.floor(y + vertical_radius) + 1)
                z0 = max(0, math.floor(z - radius) - cz * 16 - 1)
                z1 = min(16, math.floor(z + radius) - cz * 16 + 1)

                touches_lava = False
                for lx in range(x0, x1):
                    if touches_lava:
                        break
                    for lz in range(z0, z1):
                        edge_column = lx in (x0, x1 - 1) or lz in (z0, z1 - 1)
                        ys = range(y1 + 1, y0 - 2, -1) if edge_column else (y1 + 1, y0 - 1)
                        for yy in ys:
                            if 0 <= yy < 128 and data[(lx * 16 + lz) * 128 + yy] == lava:
                                touches_lava = True
                                break
                        if touches_lava:
                            break

                if not touches_lava:
                    for lx in range(x0, x1):
                        nx = ((lx + cx * 16) + 0.5 - x) / radius
                        for lz in range(z0, z1):
                            nz = ((lz + cz * 16) + 0.5 - z) / radius
                            index = (lx * 16 + lz) * 128 + y1
                            for yy in range(y1 - 1, y0 - 1, -1):
                                ny = (yy + 0.5 - y) / vertical_radius
                                if ny > -0.7 and nx * nx + ny * ny + nz * nz < 1.0:
                                    if data[index] == netherrack:
                                        data[index] = 0
                                index -= 1
                if is_large:
                    break
            step += 1

    def height_at(self, x: int, z: int) -> int:
        return 64

    def chunk_surface_range(self, cx: int, cz: int) -> tuple[int, int]:
        return (0, 127)

    def populate_chunk(self, world, cx: int, cz: int) -> None:
        rng = JavaRandom(cx * 341873128712 + cz * 132897987541)
        for _ in range(16 * 16):
            rng.next_double()  
            rng.next_double()  
            rng.next_double()  
            for y in range(127, -1, -1):
                if y >= 127 - rng.next_int(5):
                    continue
                rng.next_int(5)  
        start_x, start_z = cx * 16, cz * 16
        was_placing = bool(getattr(world, "_placing_generated_features", False))
        world._placing_generated_features = True

        def block(x, y, z):
            if y < 0 or y >= 128:
                return None
            return world.get_block_type(x, y, z)

        def set_generated(x, y, z, name):
            if 0 <= y < 128:
                world.set_block(x, y, z, name, immediate_mesh=False)

        def gen_lava(x, y, z):
            if block(x, y + 1, z) != "netherrack":
                return False
            if block(x, y, z) not in (None, "netherrack"):
                return False
            neighbors = ((-1,0,0),(1,0,0),(0,0,-1),(0,0,1),(0,-1,0))
            solid = sum(block(x+dx,y+dy,z+dz) == "netherrack" for dx,dy,dz in neighbors)
            air = sum(block(x+dx,y+dy,z+dz) is None for dx,dy,dz in neighbors)
            if solid == 4 and air == 1:
                set_generated(x, y, z, "lava")
                try:
                    from game.block.ticks import tick_stationary_lava
                    tick_stationary_lava(world, x, y, z, rng)
                except (ImportError, AttributeError):
                    pass
                return True
            return False

        def gen_fire(x, y, z):
            for _ in range(64):
                px = x + rng.next_int(8) - rng.next_int(8)
                py = y + rng.next_int(4) - rng.next_int(4)
                pz = z + rng.next_int(8) - rng.next_int(8)
                if block(px, py, pz) is None and block(px, py - 1, pz) == "netherrack":
                    set_generated(px, py, pz, "fire")

        def gen_glowstone(x, y, z):
            if block(x, y, z) is not None or block(x, y + 1, z) != "netherrack":
                return False
            set_generated(x, y, z, "glowstone")
            for _ in range(1500):
                px = x + rng.next_int(8) - rng.next_int(8)
                py = y - rng.next_int(12)
                pz = z + rng.next_int(8) - rng.next_int(8)
                if block(px, py, pz) is not None:
                    continue
                adjacent_glow = sum(
                    block(px+dx,py+dy,pz+dz) == "glowstone"
                    for dx,dy,dz in ((-1,0,0),(1,0,0),(0,-1,0),(0,1,0),(0,0,-1),(0,0,1))
                )
                if adjacent_glow == 1:
                    set_generated(px, py, pz, "glowstone")
            return True

        def gen_mushroom(name, x, y, z):
            for _ in range(64):
                px = x + rng.next_int(8) - rng.next_int(8)
                py = y + rng.next_int(4) - rng.next_int(4)
                pz = z + rng.next_int(8) - rng.next_int(8)
                if block(px, py, pz) is not None or block(px, py - 1, pz) is None:
                    continue
                from game.block import Block
                below = block(px, py - 1, pz)
                below_def = Block._block_defs.get(below, {})
                opaque_support = (
                    bool(below_def.get("collision", False))
                    and below_def.get("model", "cube") == "cube"
                    and below_def.get("nCull", "all") == "all"
                    and not below_def.get("transparent", False)
                )
                if not opaque_support or int(world.get_light(px, py, pz)) > 13:
                    continue
                too_bright = False
                for dx in range(-1, 2):
                    for dy in range(-1, 2):
                        for dz in range(-1, 2):
                            emitter = Block._block_defs.get(block(px + dx, py + dy, pz + dz), {})
                            light_value = int(emitter.get("lightValue", 0))
                            if light_value - abs(dx) - abs(dy) - abs(dz) > 13:
                                too_bright = True
                                break
                        if too_bright:
                            break
                    if too_bright:
                        break
                if not too_bright:
                    set_generated(px, py, pz, name)

        try:
            for _ in range(8):
                x = start_x + rng.next_int(16) + 8
                y = rng.next_int(120) + 4
                z = start_z + rng.next_int(16) + 8
                gen_lava(x, y, z)

            fire_count = rng.next_int(rng.next_int(10) + 1) + 1
            for _ in range(fire_count):
                x = start_x + rng.next_int(16) + 8
                y = rng.next_int(120) + 4
                z = start_z + rng.next_int(16) + 8
                gen_fire(x, y, z)

            glowstone_count = rng.next_int(rng.next_int(10) + 1)
            for _ in range(glowstone_count):
                x = start_x + rng.next_int(16) + 8
                y = rng.next_int(120) + 4
                z = start_z + rng.next_int(16) + 8
                gen_glowstone(x, y, z)
            for _ in range(10):
                x = start_x + rng.next_int(16) + 8
                y = rng.next_int(128)
                z = start_z + rng.next_int(16) + 8
                gen_glowstone(x, y, z)

            if rng.next_int(1) == 0:
                x = start_x + rng.next_int(16) + 8
                y = rng.next_int(128)
                z = start_z + rng.next_int(16) + 8
                gen_mushroom("brown_mushroom", x, y, z)
            if rng.next_int(1) == 0:
                x = start_x + rng.next_int(16) + 8
                y = rng.next_int(128)
                z = start_z + rng.next_int(16) + 8
                gen_mushroom("red_mushroom", x, y, z)
        finally:
            world._placing_generated_features = was_placing

