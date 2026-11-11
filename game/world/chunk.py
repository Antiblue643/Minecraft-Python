from OpenGL.GL import (
    glGenBuffers, glBindBuffer, glBufferData, glBufferSubData, glDeleteBuffers,
    GL_ARRAY_BUFFER, GL_DYNAMIC_DRAW, GL_FLOAT,
    glEnableClientState, glDisableClientState,
    GL_VERTEX_ARRAY, GL_TEXTURE_COORD_ARRAY, GL_COLOR_ARRAY,
    glVertexPointer, glTexCoordPointer, glColorPointer, glDrawArrays,
    glEnable, glDisable, GL_TEXTURE_2D, GL_CULL_FACE,
    GL_TEXTURE0, glActiveTexture,
    GL_TRIANGLES, glColor4f,
    GL_BLEND, GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA, glBlendFunc,
    glDepthMask, GL_TRUE, GL_FALSE,
    GL_ALPHA_TEST, GL_GREATER, glAlphaFunc,
    glPushMatrix, glPopMatrix, glTranslatef,
)
from ctypes import c_void_p
from contextlib import nullcontext
import struct
import time
import zlib
from array import array

import numpy as np

from game.block import Block
from game.block.fire_rules import fire_render_flags, is_normal_cube
from game.world.lighting_numba import NUMBA_AVAILABLE as LIGHTING_NUMBA_AVAILABLE, np as _lighting_np, initialize_skylight as _numba_initialize_skylight
from game.block.mesh_rules import (face_neighbor_direction, face_is_hidden,
                             definition_for_id, PARTIAL_MODELS,
                             quad_on_voxel_boundary)
from game.rendering.fluid_rendering import build_fluid_mesh, FLUID_TYPES
from game.rendering.transparency_sort import (
    TransparentMeshCache, FLOATS_PER_QUAD,
)


_STRIDE = 32
_UV_OFFSET = c_void_p(12)
_COLOR_OFFSET = c_void_p(20)

_K_PLAIN, _K_META, _K_DOOR, _K_CHEST, _K_WIRE, _K_FIRE, _K_FLUID, _K_FENCE = range(8)

try:
    from numba import njit as _njit

    @_njit(cache=True, nogil=True)
    def _column_all_solid(blocks, base, y0, y1, solid_lut):
        for y in range(y0, y1):
            if solid_lut[blocks[base + y]] == 0:
                return False
        return True

    _COLUMN_SOLID_NUMBA = True
except Exception:
    def _column_all_solid(blocks, base, y0, y1, solid_lut):
        for y in range(y0, y1):
            if solid_lut[blocks[base + y]] == 0:
                return False
        return True

    _COLUMN_SOLID_NUMBA = False
def _leaves_tint(meta, biome_tint=None):
    value = int(meta) & 15
    if value & 1:  
        return (97, 153, 97)  
    if value & 2:  
        return (128, 167, 85)  
    return biome_tint


_META_KEYED_MESH_TYPES = frozenset((
    "torch", "redstone_torch", "unlit_redstone_torch", "lever",
    "stone_button", "stone_pressure_plate", "wooden_pressure_plate",
    "ladder", "furnace", "furnace_lit", "dispenser",
    "rail", "crops", "farmland",
    "sapling", "stairs", "cobblestone_stairs",
    "pumpkin", "jack_o_lantern", "wool", "log", "leaves",
    "stone_slab", "double_stone_slab", "redstone_repeater", "redstone_repeater_on", "bed",
))
_FACE_NEIGHBOR_INDEX = {
    (-1, 0, 0): 0, (1, 0, 0): 1,
    (0, -1, 0): 2, (0, 1, 0): 3,
    (0, 0, -1): 4, (0, 0, 1): 5,
    (0, 0, 0): 6,
}
class Chunk:
    def __init__(self, world, cx, cz, width, height, depth, renderer,
                 generated_data=None):
        self.world = world
        self.cx = cx
        self.cz = cz
        self.width = width
        self.height = height
        self.depth = depth
        self.renderer = renderer

        volume = width * height * depth
        self.blocks = None
        self.metadata = bytearray(volume)
        self.skylight = bytearray(volume)
        self.blocklight = bytearray(volume)
        self.heightmap = bytearray(width * depth)
        self._block_type_to_id = world.block_type_to_id
        self._id_to_block_type = world.id_to_block_type

        self.section_height = 4
        self.section_count = (height + self.section_height - 1) // self.section_height
        self._sections = [
            {
                "index": _section_index,
                "mesh_opaque": b"", "mesh_cutout": b"", "mesh_cutout_culled": b"", "mesh_transparent": b"",
                "count_opaque": 0, "count_cutout": 0, "count_cutout_culled": 0, "count_transparent": 0,
                "first_opaque": 0, "first_cutout": 0, "first_cutout_culled": 0, "first_transparent": 0,
                "built": False,
            }
            for _section_index in range(self.section_count)
        ]
        self._vbo_opaque = None
        self._vbo_cutout = None
        self._vbo_cutout_culled = None
        self._vbo_transparent = None
        self._count_opaque = 0
        self._count_cutout = 0
        self._count_cutout_culled = 0
        self._count_transparent = 0
        self._has_mesh = False
        self._mesh_all_air_cache = None
        self.dirty_sections = set(range(self.section_count))
        self.dirty = True
        note = getattr(world, "note_chunk_mesh_dirty", None)
        if callable(note):
            note((cx, cz))
        self.light_ready = False
        self.terrain_populated = False
        self._source_block_ids = None
        self._source_internal_blocks = None
        self._source_metadata = None
        self._mcpy_block_names = {}
        self._saved_fingerprint = None
        self._saved_populated = False
        self._saved_entity_count = 0

        self.start_x = self.cx * self.width
        self.start_z = self.cz * self.depth
        self._generate_grid(generated_data)

    def fingerprint(self):
        crc = zlib.crc32(self.blocks)
        for data in (self.metadata, self.skylight, self.blocklight, self.heightmap):
            crc = zlib.crc32(data, crc)
        return crc

    def mark_saved_state(self, world, fingerprint=None, entity_count=0):
        self._saved_fingerprint = self.fingerprint() if fingerprint is None else fingerprint
        self._saved_populated = self._is_populated(world)
        self._saved_entity_count = int(entity_count)

    def _is_populated(self, world):
        return bool(self.terrain_populated
                    or (self.cx, self.cz) in getattr(world, "_populated_chunks", ()))

    def needs_saving(self, world, has_items=False):
        if has_items or self._saved_fingerprint is None or self._saved_entity_count:
            return True
        if self._is_populated(world) != self._saved_populated:
            return True
        return self.fingerprint() != self._saved_fingerprint

    def _index(self, x, y, z):
        lx = x - self.start_x
        lz = z - self.start_z
        if not (0 <= lx < self.width and 0 <= lz < self.depth and 0 <= y < self.height):
            return None
        return (lx * self.depth + lz) * self.height + y

    def _generate_grid(self, generated_data=None):
        if generated_data is None:
            generated_data = self.world.terrain.generate_chunk_data(
                self.cx, self.cz, self.width, self.depth, self._block_type_to_id
            )
        expected_volume = self.width * self.height * self.depth
        if isinstance(generated_data, bytearray) and len(generated_data) == expected_volume:
            self.blocks = generated_data
        else:
            self.blocks = bytearray(expected_volume)
            self.blocks[:] = generated_data
        self.surface_min_y, self.surface_max_y = self.world.terrain.chunk_surface_range(
            self.cx, self.cz
        )

    def _local_index(self, lx, y, lz):
        return (lx * self.depth + lz) * self.height + y

    def _opacity_for_id(self, block_id):
        if block_id == 0:
            return 0
        name = self._id_to_block_type[block_id] if block_id < len(self._id_to_block_type) else None
        if not name:
            return 255
        return int(Block._block_defs.get(name, {}).get("lightOpacity", 255))

    def _light_value_for_id(self, block_id):
        if block_id == 0:
            return 0
        name = self._id_to_block_type[block_id] if block_id < len(self._id_to_block_type) else None
        if not name:
            return 0
        return int(Block._block_defs.get(name, {}).get("lightValue", 0))

    def get_skylight(self, x, y, z):
        idx = self._index(x, y, z)
        if idx is None:
            return 0
        return self.skylight[idx]

    def get_blocklight(self, x, y, z):
        idx = self._index(x, y, z)
        if idx is None:
            return 0
        return self.blocklight[idx]

    def get_combined_light(self, x, y, z):
        idx = self._index(x, y, z)
        if idx is None:
            return 0
        sky = self.skylight[idx]
        block = self.blocklight[idx]
        lighting = getattr(self.world, "lighting", None)
        if lighting is not None:
            return lighting.combined_light(sky, block)
        return max(sky, block)

    def set_skylight_local(self, lx, y, lz, level):
        self.skylight[self._local_index(lx, y, lz)] = max(0, min(15, int(level)))

    def set_blocklight_local(self, lx, y, lz, level):
        self.blocklight[self._local_index(lx, y, lz)] = max(0, min(15, int(level)))

    def compute_heightmap_and_skylight(self):
        Block._load_defs()
        width, depth, height = self.width, self.depth, self.height
        if LIGHTING_NUMBA_AVAILABLE and hasattr(self.world, "_get_lighting_luts"):
            opacity_lut, _light_lut = self.world._get_lighting_luts()
            _numba_initialize_skylight(
                _lighting_np.frombuffer(self.blocks, dtype=_lighting_np.uint8),
                _lighting_np.frombuffer(self.skylight, dtype=_lighting_np.uint8),
                _lighting_np.frombuffer(self.heightmap, dtype=_lighting_np.uint8),
                opacity_lut, width, depth, height,
            )
            self.light_ready = True
            return

        blocks = self.blocks
        skylight = self.skylight
        heightmap = self.heightmap

        for lx in range(width):
            for lz in range(depth):
                base = (lx * depth + lz) * height
                top = height
                for y in range(height - 1, -1, -1):
                    bid = blocks[base + y]
                    if bid and self._opacity_for_id(bid) > 0:
                        top = y + 1
                        break
                else:
                    top = 0
                heightmap[lz * width + lx] = top & 0xFF

                for y in range(top, height):
                    skylight[base + y] = 15

                light = 15
                for y in range(top - 1, -1, -1):
                    bid = blocks[base + y]
                    opacity = self._opacity_for_id(bid)
                    if opacity == 0:
                        opacity = 1
                    light -= opacity
                    if light < 0:
                        light = 0
                    skylight[base + y] = light
                    if light == 0 and opacity >= 15:
                        for yy in range(y):
                            skylight[base + yy] = 0
                        break

        self.light_ready = True

    def seed_block_lights(self):
        Block._load_defs()
        width, depth, height = self.width, self.depth, self.height
        blocks = self.blocks
        blocklight = self.blocklight
        sources = []
        for lx in range(width):
            for lz in range(depth):
                base = (lx * depth + lz) * height
                for y in range(height):
                    bid = blocks[base + y]
                    if bid == 0:
                        continue
                    lv = self._light_value_for_id(bid)
                    if lv > 0:
                        blocklight[base + y] = lv
                        sources.append((lx, y, lz, lv))
        return sources

    def get_height_value(self, lx, lz):
        return self.heightmap[lz * self.width + lx]

    def _sample_light_local(self, lx, y, lz):
        height = self.height
        if y < 0:
            return 0
        if y >= height:
            lighting = getattr(self.world, "lighting", None)
            if lighting is not None:
                return max(0, 15 - lighting.skylight_subtracted)
            return 15

        if 0 <= lx < self.width and 0 <= lz < self.depth:
            idx = self._local_index(lx, y, lz)
            sky = self.skylight[idx]
            block = self.blocklight[idx]
        else:
            ncx, ncz = self.cx, self.cz
            n_lx, n_lz = lx, lz
            if lx < 0:
                ncx -= 1
                n_lx = self.width + lx
            elif lx >= self.width:
                ncx += 1
                n_lx = lx - self.width
            if lz < 0:
                ncz -= 1
                n_lz = self.depth + lz
            elif lz >= self.depth:
                ncz += 1
                n_lz = lz - self.depth
            if not (0 <= n_lx < self.width and 0 <= n_lz < self.depth):
                return 15
            neighbor = self.world.chunks.get((ncx, ncz))
            if neighbor is None or not neighbor.light_ready:
                return 15
            idx = neighbor._local_index(n_lx, y, n_lz)
            light_len = len(neighbor.skylight)
            if idx < 0 or idx >= light_len:
                return 15
            sky = neighbor.skylight[idx]
            block = neighbor.blocklight[idx] if idx < len(neighbor.blocklight) else 0

        lighting = getattr(self.world, "lighting", None)
        if lighting is not None:
            return lighting.combined_light(sky, block)
        return max(sky, block)


    def get_metadata(self, x, y, z):
        idx = self._index(x, y, z)
        if idx is None:
            return 0
        return self.metadata[idx]

    def set_metadata(self, x, y, z, meta):
        idx = self._index(x, y, z)
        if idx is None:
            return
        self.metadata[idx] = int(meta) & 15

    def get_block_type(self, x, y, z):
        idx = self._index(x, y, z)
        if idx is None:
            return None
        block_id = self.blocks[idx]
        if block_id == 0:
            return None
        return self._id_to_block_type[block_id]

    def get_block(self, x, y, z):
        block_type = self.get_block_type(x, y, z)
        if block_type is None:
            return None
        block = Block(block_type, (x, y, z), world=self.world, renderer=self.renderer)
        block._mesh_meta = self.get_metadata(x, y, z)
        return block

    def _invalidate_mesh_air_cache(self):
        self._mesh_all_air_cache = None

    def _mark_section_dirty(self, section_index):
        if 0 <= section_index < self.section_count:
            self.dirty_sections.add(section_index)
            self.dirty = True
            world = getattr(self, "world", None)
            note = getattr(world, "note_chunk_mesh_dirty", None) if world is not None else None
            if callable(note):
                note((self.cx, self.cz))

    def mark_dirty_at(self, y):
        section_index = max(0, min(self.section_count - 1, int(y) // self.section_height))
        self._mark_section_dirty(section_index)
        if int(y) % self.section_height == 0:
            self._mark_section_dirty(section_index - 1)
        if int(y) % self.section_height == self.section_height - 1:
            self._mark_section_dirty(section_index + 1)

    def mark_all_dirty(self):
        self.dirty_sections.update(range(self.section_count))
        self.dirty = True
        world = getattr(self, "world", None)
        note = getattr(world, "note_chunk_mesh_dirty", None) if world is not None else None
        if callable(note):
            note((self.cx, self.cz))

    def mark_lighting_dirty(self):
        self.mark_all_dirty()

    def set_block(self, x, y, z, block, meta=0):
        idx = self._index(x, y, z)
        if idx is None:
            return
        if block is None:
            block_id = 0
            meta = 0
        else:
            block_type = block.block_type if isinstance(block, Block) else str(block)
            block_id = self._block_type_to_id.get(block_type)
            if block_id is None:
                block_id = self.world._ensure_block_type_id(block_type)
        meta = int(meta) & 15
        if self.blocks[idx] == block_id and self.metadata[idx] == meta:
            return
        old_block_id = self.blocks[idx]
        self.blocks[idx] = block_id
        self.metadata[idx] = meta
        if old_block_id != block_id:
            self._invalidate_mesh_air_cache()
            self._mcpy_block_names.pop(idx, None)
        self.mark_dirty_at(y)

    def remove_block(self, x, y, z):
        idx = self._index(x, y, z)
        if idx is not None and self.blocks[idx] != 0:
            self.blocks[idx] = 0
            self._invalidate_mesh_air_cache()
            self.metadata[idx] = 0
            self._mcpy_block_names.pop(idx, None)
            self.mark_dirty_at(y)

    def _neighbor_type(self, x, y, z):
        if not (0 <= y < self.height):
            return None
        if (self.start_x <= x < self.start_x + self.width and
                self.start_z <= z < self.start_z + self.depth):
            return self.get_block_type(x, y, z)
        return self.world.get_block_type(x, y, z)

    def _has_visible_face(self, block_type, x, y, z, definition):
        if definition.get("model", "cube") != "cube":
            return True

        n_cull = definition.get("nCull", "all")
        if n_cull == "none":
            return True

        for face_name, direction, _quad, _cull_backface in Block._CUBE_FACES:
            nx, ny, nz = x + direction[0], y + direction[1], z + direction[2]
            neighbor_type = self._neighbor_type(nx, ny, nz)
            neighbor_def = Block._block_defs.get(neighbor_type, {}) if neighbor_type else {}

            if n_cull == "all":
                hidden = neighbor_type is not None and neighbor_def.get("nCull", "all") == "all"
            elif n_cull == "type":
                hidden = neighbor_type == block_type
            elif n_cull == "liquid":
                hidden = neighbor_type is not None
            else:
                hidden = False

            if not hidden:
                return True
        return False

    @staticmethod
    def _faces_to_bytes(faces):
        buf = bytearray(len(faces) * 6 * 8 * 4)
        mv = memoryview(buf)
        offset = 0
        pack = struct.pack_into
        for rotated, uvs, _cull, color in faces:
            r, g, b = color
            for i in (0, 1, 2):
                x, y, z = rotated[i]
                u, v = uvs[i]
                pack("<ffffffff", mv, offset, x, y, z, u, v, r, g, b)
                offset += 32
            for i in (0, 2, 3):
                x, y, z = rotated[i]
                u, v = uvs[i]
                pack("<ffffffff", mv, offset, x, y, z, u, v, r, g, b)
                offset += 32
        return buf, len(faces) * 6

    def _upload_vbo(self, old_vbo, data):
        if not data:
            return old_vbo

        vbo = int(old_vbo) if old_vbo is not None else int(glGenBuffers(1))
        payload = data.tobytes() if hasattr(data, "tobytes") else bytes(data)
        glBindBuffer(GL_ARRAY_BUFFER, vbo)
        glBufferData(GL_ARRAY_BUFFER, len(payload), None, GL_DYNAMIC_DRAW)
        glBufferSubData(GL_ARRAY_BUFFER, 0, len(payload), payload)
        glBindBuffer(GL_ARRAY_BUFFER, 0)
        return vbo

    _mesh_template_cache = {}
    _sorted_transparent_vbo = None
    _transparent_mesh_cache = TransparentMeshCache()
    _triangle_order = (0, 1, 2, 0, 2, 3)

    def _mesh_template(self, block_type, meta=0, door_mirror_right=False, chest_faces=None,
                       wire_flags=0):
        if block_type == "chest" and chest_faces is not None:
            cache_key = (block_type, "face_textures", tuple(int(v) for v in chest_faces))
        elif block_type == "portal":
            cache_key = (block_type, int(wire_flags) & 63)
        else:
            cache_key = (
                (block_type, int(meta) & 15, bool(door_mirror_right)) if block_type in ("wooden_door", "iron_door")
                else (block_type, int(meta) & 255) if block_type == "fire"
                else (block_type, int(meta) & 15, int(wire_flags) & 255) if block_type in ("redstone_wire", "fence")
                else (block_type, int(meta) & 3, bool(getattr(Block, "_fancy_graphics", True))) if block_type == "leaves"
                else (block_type, int(meta) & 15) if block_type in (
                    "bed", "torch", "redstone_torch", "unlit_redstone_torch", "lever",
                    "stone_button", "stone_pressure_plate", "wooden_pressure_plate", "ladder", "furnace", "dispenser",
                    "furnace_lit", "chest", "rail", "crops", "farmland", "sapling",
                    "stairs", "cobblestone_stairs", "pumpkin", "jack_o_lantern", "wool", "cake", "log",
                    "stone_slab", "double_stone_slab", "redstone_repeater", "redstone_repeater_on",
                )
                else (block_type, bool(getattr(Block, "_fancy_graphics", True))) if block_type == "leaves"
                else (block_type, "snowy") if block_type == "grass_block" and int(meta) == 1
                else block_type
            )
        template = self._mesh_template_cache.get(cache_key)
        if template is not None:
            return template

        Block._load_defs()
        block = Block(block_type, (0, 0, 0), world=None, renderer=None)
        block._mesh_meta = (
            int(meta) & 255 if block_type == "fire"
            else int(meta) & 15
        )
        meta_faces = block.definition.get("metaFaces")
        if block_type == "wool":
            from game.block.wool import wool_texture_index
            block.faces = wool_texture_index(meta, block.definition)
        elif block_type == "leaves":
            base_tile = 52 if bool(getattr(Block, "_fancy_graphics", True)) else 53
            block.faces = base_tile + (80 if (int(meta) & 3) == 1 else 0)
        elif meta_faces and int(meta) & 15:
            variant_meta = (int(meta) & 3) if block_type == "leaves" else (int(meta) & 15)
            variant = meta_faces.get(str(variant_meta))
            if variant is not None:
                block.faces = variant
        if block_type == "redstone_wire":
            block._mesh_wire_flags = int(wire_flags) & 255
        if block_type == "fence":
            block._mesh_fence_flags = int(wire_flags) & 255
        if block_type == "portal":
            block._mesh_portal_neighbors = tuple(
                bool(int(wire_flags) & (1 << bit)) for bit in range(6)
            )
        if block_type == "chest" and chest_faces is not None:
            block.faces = [int(v) for v in chest_faces]
            block._chest_faces_override = True
        if block_type in ("wooden_door", "iron_door"):
            block._door_mirror_right = bool(door_mirror_right)
        mesh_data = block.get_mesh_data()
        template = []
        if block_type == "bed":
            for mesh in mesh_data:
                direction, vertices, uvs, cull_backface, is_transparent, color = mesh
                template.append((direction, vertices, uvs, cull_backface, is_transparent, color))
        elif block.model_name == "cube":
            sources = zip(Block._CUBE_FACES, mesh_data)
            for (name, direction, _quad, _face_cull), mesh in sources:
                vertices, uvs, cull_backface, is_transparent, color = mesh
                template.append((direction, vertices, uvs, cull_backface, is_transparent, color))
        elif block.model_name in ("farmland", "slab", "stairs", "door", "lever", "pressure_plate", "button", "snow_layer", "cactus", "fence", "portal", "redstone_repeater") or block_type in (
            "farmland", "stone_slab", "stairs", "cobblestone_stairs", "wooden_door", "iron_door", "cactus",
            "lever", "stone_button", "stone_pressure_plate", "wooden_pressure_plate", "snow", "fence",
            "redstone_repeater", "redstone_repeater_on",
        ):
            for mesh in mesh_data:
                direction, vertices, uvs, cull_backface, is_transparent, color = mesh
                template.append((direction, vertices, uvs, cull_backface, is_transparent, color))
        elif block.model_name in ("fire", "crops", "torch", "ladder", "rail", "redstone_wire") or block_type in (
            "fire", "crops", "torch", "redstone_torch", "unlit_redstone_torch", "ladder", "rail", "redstone_wire",
        ):
            for mesh in mesh_data:
                vertices, uvs, cull_backface, is_transparent, color = mesh
                template.append(((0, 0, 0), vertices, uvs, cull_backface, is_transparent, color))
        else:
            for (_name, model_data), mesh in zip(block.model.items(), mesh_data):
                direction = block._face_direction(tuple(model_data[3:6]))
                direction = face_neighbor_direction(block.block_type, block.model_name, direction)
                vertices, uvs, cull_backface, is_transparent, color = mesh
                template.append((direction, vertices, uvs, cull_backface, is_transparent, color))

        self._mesh_template_cache[cache_key] = template
        return template

    def _door_is_right_leaf(self, lx, y, lz, meta):
        if self.world is None:
            return False
        door_y = y - 1 if (int(meta) & 8) else y
        wx = self.start_x + int(lx)
        wz = self.start_z + int(lz)
        return bool(self.world.is_door_mirrored(wx, door_y, wz))

    def _neighbor_id(self, lx, y, lz, dx, dy, dz):
        ny = y + dy
        if ny < 0 or ny >= self.height:
            return 0
        nx, nz = lx + dx, lz + dz
        if 0 <= nx < self.width and 0 <= nz < self.depth:
            return self.blocks[(nx * self.depth + nz) * self.height + ny]

        if nx < 0:
            neighbor = self.world.chunks.get((self.cx - 1, self.cz))
            nx = self.width - 1
        elif nx >= self.width:
            neighbor = self.world.chunks.get((self.cx + 1, self.cz))
            nx = 0
        elif nz < 0:
            neighbor = self.world.chunks.get((self.cx, self.cz - 1))
            nz = self.depth - 1
        else:
            neighbor = self.world.chunks.get((self.cx, self.cz + 1))
            nz = 0
        if neighbor is None:
            return 0
        return neighbor.blocks[(nx * neighbor.depth + nz) * neighbor.height + ny]

    @staticmethod
    def _is_face_hidden(n_cull, block_id, neighbor_id, defs_by_id, is_transparent=False):
        return face_is_hidden(n_cull, block_id, neighbor_id, defs_by_id, is_transparent)

    def _mesh_luts(self):
        world = self.world
        id_to_type = self._id_to_block_type
        n_ids = len(id_to_type)
        cache = getattr(world, "_mesh_lut_cache", None)
        if cache is not None and cache[0] == n_ids:
            return cache[1]
        Block._load_defs()
        defs_by_id = [None] * n_ids
        id_info = [None] * n_ids
        solid_lut = bytearray(256)
        skip_lut = bytearray(256)
        partial_model_lut = bytearray(256)
        for block_id in range(1, n_ids):
            btype = id_to_type[block_id]
            definition = Block._block_defs.get(btype, {})
            defs_by_id[block_id] = definition
            model = definition.get("model", "cube")
            n_cull = definition.get("nCull", "all")
            if btype in FLUID_TYPES:
                kind = _K_FLUID
            elif btype == "fire":
                kind = _K_FIRE
            elif btype == "chest":
                kind = _K_CHEST
            elif btype in ("wooden_door", "iron_door"):
                kind = _K_DOOR
            elif btype in _META_KEYED_MESH_TYPES:
                kind = _K_META
            elif btype == "redstone_wire":
                kind = _K_WIRE
            elif btype == "fence":
                kind = _K_FENCE
            else:
                kind = _K_PLAIN
            is_partial = model in PARTIAL_MODELS or bool(definition.get("partial", False))
            if block_id < 256 and is_partial:
                partial_model_lut[block_id] = 1
            id_info[block_id] = (
                n_cull, is_partial, definition.get("cutoutCullBackfaces", False),
                btype, kind,
            )
            if block_id < 256 and definition:
                if n_cull == "all" and not is_partial:
                    solid_lut[block_id] = 1
                    if model == "cube" and kind == _K_PLAIN:
                        skip_lut[block_id] = 1
        solid_translate = bytes(solid_lut)
        snow_id = id_to_type.index("snow") if "snow" in id_to_type else 0
        payload = (
            defs_by_id, id_info, solid_lut, skip_lut, partial_model_lut,
            solid_translate, snow_id, n_ids,
        )
        world._mesh_lut_cache = (n_ids, payload)
        return payload

    def _build_mesh(self, section_index=None, commit_partial=False):
        if getattr(self.world, "headless", False):
            return
        mesh_total_start = time.perf_counter()
        Block._load_defs()
        if section_index is None:
            section_indices = sorted(self.dirty_sections)
            if not section_indices and self.dirty:
                section_indices = list(range(self.section_count))
        else:
            section_indices = [section_index] if section_index in self.dirty_sections else []
        if not section_indices:
            self.dirty = bool(self.dirty_sections)
            return

        from engine.lighting import face_color as _face_color, face_shade as _face_shade
        from game.rendering.smooth_lighting import (
            block_ao_occludes, is_full_voxel_face, smooth_face_colors,
        )
        hell_lighting = bool(getattr(self.world, "is_hell_world", False))
        smooth_lighting_enabled = bool(
            getattr(self.world, "smooth_lighting_enabled", True)
        )

        (defs_by_id, id_info, solid_lut, skip_lut, partial_model_lut,
         solid_translate, snow_id, n_ids) = self._mesh_luts()
        id_to_type = self._id_to_block_type
        ao_occluder_lut = bytearray(256)
        for ao_block_id in range(1, min(n_ids, len(ao_occluder_lut))):
            ao_block_type = id_to_type[ao_block_id]
            ao_definition = defs_by_id[ao_block_id]
            ao_occluder_lut[ao_block_id] = int(
                block_ao_occludes(ao_block_type, ao_definition)
            )

        blocks = self.blocks
        metadata = self.metadata
        skylight = self.skylight
        blocklight = self.blocklight
        width = self.width
        depth = self.depth
        height = self.height
        all_air = getattr(self, "_mesh_all_air_cache", None)
        if all_air is None:
            all_air = blocks.count(0) == len(blocks)
            self._mesh_all_air_cache = all_air
        if all_air:
            for section_idx in section_indices:
                section = self._sections[section_idx]
                section["mesh_opaque"] = b""
                section["mesh_cutout"] = b""
                section["mesh_cutout_culled"] = b""
                section["mesh_transparent"] = b""
                section["count_opaque"] = 0
                section["count_cutout"] = 0
                section["count_cutout_culled"] = 0
                section["count_transparent"] = 0
                section["built"] = True
                self.dirty_sections.discard(section_idx)
            self.dirty = bool(self.dirty_sections)
            profiler = getattr(self.world, "profiler", None)
            if profiler is not None:
                profiler.record_ms("mesh.scan", 0.0)
                profiler.record_ms("mesh.total", (time.perf_counter() - mesh_total_start) * 1000.0)
            return
        cx_self, cz_self = self.cx, self.cz
        world_chunks = self.world.chunks
        neighbor_id_fn = self._neighbor_id
        is_hidden = self._is_face_hidden
        mesh_template = self._mesh_template
        start_x = self.start_x
        start_z = self.start_z

        biome_provider = getattr(self.world, "biome_provider", None)
        if biome_provider is None:
            biome_provider = getattr(getattr(self.world, "terrain", None), "biomes", None)
        biome_tints = {}
        if biome_provider is not None:
            from game.app.texturepacks import get_active_texture_pack
            tint_key = (id(biome_provider), get_active_texture_pack())
            tint_cache = getattr(self, "_biome_tint_cache", None)
            if tint_cache is not None and tint_cache[0] == tint_key:
                biome_tints = tint_cache[1]
            else:
                for tint_x in range(width):
                    for tint_z in range(depth):
                        world_x = start_x + tint_x
                        world_z = start_z + tint_z
                        biome_tints[(tint_x, tint_z)] = (
                            biome_provider.get_grass_tint(world_x, world_z),
                            biome_provider.get_foliage_tint(world_x, world_z),
                        )
                self._biome_tint_cache = (tint_key, biome_tints)

        lighting = getattr(self.world, "lighting", None)
        sky_sub = lighting.skylight_subtracted if lighting is not None else None

        def sample_light_components(lx, y, lz):
            if y < 0:
                return 0, 0
            if y >= height:
                return (0, 0) if hell_lighting else (15, 0)
            if 0 <= lx < width and 0 <= lz < depth:
                idx = (lx * depth + lz) * height + y
                return (0 if hell_lighting else int(skylight[idx])), int(blocklight[idx])

            ncx, ncz = cx_self, cz_self
            n_lx, n_lz = lx, lz
            if lx < 0:
                ncx -= 1
                n_lx = width + lx
            elif lx >= width:
                ncx += 1
                n_lx = lx - width
            if lz < 0:
                ncz -= 1
                n_lz = depth + lz
            elif lz >= depth:
                ncz += 1
                n_lz = lz - depth
            if not (0 <= n_lx < width and 0 <= n_lz < depth):
                return (0, 0) if hell_lighting else (26, 0)
            neighbor = world_chunks.get((ncx, ncz))
            if neighbor is None or not neighbor.light_ready:
                return (0, 0) if hell_lighting else (26, 0)
            idx = (n_lx * neighbor.depth + n_lz) * neighbor.height + y
            n_sky = neighbor.skylight
            if idx < 0 or idx >= len(n_sky):
                return (0, 0) if hell_lighting else (26, 0)
            n_block = neighbor.blocklight
            return (
                0 if hell_lighting else int(n_sky[idx]),
                int(n_block[idx]) if idx < len(n_block) else 0,
            )

        def sample_light(lx, y, lz) -> int:
            sky, block = sample_light_components(lx, y, lz)
            effective_sky = sky if sky_sub is None else max(0, sky - sky_sub)
            return min(15, max(effective_sky, block))

        def sample_block_id(lx, y, lz):
            if y < 0 or y >= height:
                return 0
            if 0 <= lx < width and 0 <= lz < depth:
                return blocks[(lx * depth + lz) * height + y]

            chunk_dx = -1 if lx < 0 else (1 if lx >= width else 0)
            chunk_dz = -1 if lz < 0 else (1 if lz >= depth else 0)
            neighbor = world_chunks.get((cx_self + chunk_dx, cz_self + chunk_dz))
            if neighbor is None:
                return 0
            neighbor_x = lx % neighbor.width
            neighbor_z = lz % neighbor.depth
            index = (neighbor_x * neighbor.depth + neighbor_z) * neighbor.height + y
            if index < 0 or index >= len(neighbor.blocks):
                return 0
            return neighbor.blocks[index]

        def sample_ao_occluder(position):
            block_id = sample_block_id(*position)
            return bool(0 <= block_id < len(ao_occluder_lut)
                        and ao_occluder_lut[block_id])

        smooth_light_cache = {}
        smooth_ao_cache = {}

        def sample_smooth_light(position):
            value = smooth_light_cache.get(position)
            if value is None:
                value = sample_light(*position)
                smooth_light_cache[position] = value
            return value

        def sample_smooth_ao_occluder(position):
            value = smooth_ao_cache.get(position)
            if value is None:
                value = sample_ao_occluder(position)
                smooth_ao_cache[position] = value
            return value

        west = world_chunks.get((cx_self - 1, cz_self))
        east = world_chunks.get((cx_self + 1, cz_self))
        north = world_chunks.get((cx_self, cz_self - 1))
        south = world_chunks.get((cx_self, cz_self + 1))
        air_column = bytes(height)
        west_blocks = west.blocks if west is not None else None
        east_blocks = east.blocks if east is not None else None
        north_blocks = north.blocks if north is not None else None
        south_blocks = south.blocks if south is not None else None
        blocks_u8 = np.frombuffer(blocks, dtype=np.uint8)
        try:
            solid_lut_u8 = np.frombuffer(solid_lut, dtype=np.uint8)
        except TypeError:
            solid_lut_u8 = np.asarray(solid_lut, dtype=np.uint8)
        air_u8 = np.frombuffer(air_column, dtype=np.uint8)
        west_u8 = (
            np.frombuffer(west_blocks, dtype=np.uint8)
            if west_blocks is not None else air_u8
        )
        east_u8 = (
            np.frombuffer(east_blocks, dtype=np.uint8)
            if east_blocks is not None else air_u8
        )
        north_u8 = (
            np.frombuffer(north_blocks, dtype=np.uint8)
            if north_blocks is not None else air_u8
        )
        south_u8 = (
            np.frombuffer(south_blocks, dtype=np.uint8)
            if south_blocks is not None else air_u8
        )
        x_stride = depth * height
        top_y = height - 1

        profiler = getattr(self.world, "profiler", None)
        mesh_scan_ms = 0.0
        mesh_pack_ms = 0.0
        hidden_cache = bytearray(n_ids * 256 * 2)
        face_color_cache: dict = {}
        for section_idx in section_indices:
            section = self._sections[section_idx]
            section_scan_start = time.perf_counter()
            y_start = section_idx * self.section_height
            y_end = min(height, y_start + self.section_height)
            span = y_end - y_start
            section_air = True
            for lx in range(width):
                col_base = (lx * depth) * height
                for lz in range(depth):
                    base = col_base + lz * height
                    if blocks.count(0, base + y_start, base + y_end) != span:
                        section_air = False
                        break
                if not section_air:
                    break
            if section_air:
                section["mesh_opaque"] = b""
                section["mesh_cutout"] = b""
                section["mesh_cutout_culled"] = b""
                section["mesh_transparent"] = b""
                section["count_opaque"] = 0
                section["count_cutout"] = 0
                section["count_cutout_culled"] = 0
                section["count_transparent"] = 0
                section["built"] = True
                self.dirty_sections.discard(section_idx)
                mesh_scan_ms += (time.perf_counter() - section_scan_start) * 1000.0
                continue

            templates_by_id = {}
            opaque = []
            cutout = []
            cutout_culled = []
            transparent = []
            opaque_extend = opaque.extend
            cutout_extend = cutout.extend
            cutout_culled_extend = cutout_culled.extend
            transparent_extend = transparent.extend
            fluid_corner_cache = {}

            for lx in range(width):
                for lz in range(depth):
                    base = (lx * depth + lz) * height
                    if blocks.count(0, base + y_start, base + y_end) == span:
                        continue

                    if lx > 0:
                        xm, xm_base, xm_u8 = blocks, base - x_stride, blocks_u8
                    else:
                        xm = west_blocks if west_blocks is not None else air_column
                        xm_base = ((width - 1) * depth + lz) * height if west_blocks is not None else 0
                        xm_u8 = west_u8
                    if lx < width - 1:
                        xp, xp_base, xp_u8 = blocks, base + x_stride, blocks_u8
                    else:
                        xp = east_blocks if east_blocks is not None else air_column
                        xp_base = lz * height if east_blocks is not None else 0
                        xp_u8 = east_u8
                    if lz > 0:
                        zm, zm_base, zm_u8 = blocks, base - height, blocks_u8
                    else:
                        zm = north_blocks if north_blocks is not None else air_column
                        zm_base = (lx * depth + depth - 1) * height if north_blocks is not None else 0
                        zm_u8 = north_u8
                    if lz < depth - 1:
                        zp, zp_base, zp_u8 = blocks, base + height, blocks_u8
                    else:
                        zp = south_blocks if south_blocks is not None else air_column
                        zp_base = lx * depth * height if south_blocks is not None else 0
                        zp_u8 = south_u8

                    if y_start > 0 and y_end <= top_y:
                        if (solid_lut[blocks[base + y_start - 1]]
                                and solid_lut[blocks[base + y_end]]
                                and _column_all_solid(
                                    blocks_u8, base, y_start, y_end, solid_lut_u8)
                                and _column_all_solid(
                                    xm_u8, xm_base, y_start, y_end, solid_lut_u8)
                                and _column_all_solid(
                                    xp_u8, xp_base, y_start, y_end, solid_lut_u8)
                                and _column_all_solid(
                                    zm_u8, zm_base, y_start, y_end, solid_lut_u8)
                                and _column_all_solid(
                                    zp_u8, zp_base, y_start, y_end, solid_lut_u8)):
                            continue

                    for y in range(y_start, y_end):
                        block_id = blocks[base + y]
                        if block_id == 0:
                            continue

                        if (skip_lut[block_id] and 0 < y < top_y
                                and solid_lut[blocks[base + y - 1]]
                                and solid_lut[blocks[base + y + 1]]
                                and solid_lut[xm[xm_base + y]]
                                and solid_lut[xp[xp_base + y]]
                                and solid_lut[zm[zm_base + y]]
                                and solid_lut[zp[zp_base + y]]):
                            continue

                        n_cull, definition_is_partial, definition_cutout_culled, btype, kind = id_info[block_id]
                        meta = metadata[base + y] if metadata else 0
                        if kind == _K_FIRE:
                            def _fire_neighbor(dx, dy, dz):
                                nid = neighbor_id_fn(lx, y, lz, dx, dy, dz)
                                if not nid or nid >= len(defs_by_id):
                                    return None
                                return defs_by_id[nid]
                            meta = fire_render_flags(
                                start_x + lx, y, start_z + lz,
                                _fire_neighbor(0, -1, 0),
                                _fire_neighbor(-1, 0, 0), _fire_neighbor(1, 0, 0),
                                _fire_neighbor(0, 0, -1), _fire_neighbor(0, 0, 1),
                                _fire_neighbor(0, 1, 0),
                            )
                        is_fluid = kind == _K_FLUID
                        if is_fluid:
                            block_template = build_fluid_mesh(
                                self.world, btype, start_x + lx, y,
                                start_z + lz, meta,
                                corner_cache=fluid_corner_cache,
                            )
                        else:
                            template_meta = meta
                            if kind == _K_DOOR and (meta & 8) and y > 0:
                                lower_meta = metadata[base + y - 1] & 7 if metadata else 0
                                template_meta = lower_meta | 8
                            door_mirror_right = (
                                self._door_is_right_leaf(lx, y, lz, meta)
                                if btype in ("wooden_door", "iron_door") else False
                            )
                            chest_faces = None
                            wire_flags = 0
                            if kind == _K_WIRE:
                                from game.block.redstone_wire import redstone_wire_render_flags_from_world
                                wire_flags = redstone_wire_render_flags_from_world(
                                    self.world, (start_x + lx, y, start_z + lz),
                                )
                            if kind == _K_FENCE:
                                wx, wy, wz = start_x + lx, y, start_z + lz
                                get = self.world.get_block_type
                                wire_flags = 0
                                if get(wx - 1, wy, wz) == "fence":
                                    wire_flags |= 1
                                if get(wx + 1, wy, wz) == "fence":
                                    wire_flags |= 2
                                if get(wx, wy, wz - 1) == "fence":
                                    wire_flags |= 4
                                if get(wx, wy, wz + 1) == "fence":
                                    wire_flags |= 8
                            if btype == "portal":
                                wx, wz = start_x + lx, start_z + lz
                                get = self.world.get_block_type
                                wire_flags = (
                                    int(get(wx - 1, y, wz) == "portal")
                                    | (int(get(wx + 1, y, wz) == "portal") << 1)
                                    | (int(get(wx, y, wz - 1) == "portal") << 2)
                                    | (int(get(wx, y, wz + 1) == "portal") << 3)
                                    | (int(get(wx, y - 1, wz) == "portal") << 4)
                                    | (int(get(wx, y + 1, wz) == "portal") << 5)
                                )
                            if kind == _K_CHEST:
                                from game.block.chest_render import chest_face_textures
                                chest_faces = chest_face_textures(
                                    self.world, start_x + lx, y,
                                    start_z + lz, meta,
                                )
                                tkey = (block_id, "face_textures", chest_faces)
                            elif kind == _K_DOOR:
                                tkey = (block_id, template_meta, door_mirror_right)
                            elif kind == _K_META or kind == _K_FIRE or btype == "wool":
                                tkey = (block_id, template_meta)
                            elif kind == _K_WIRE or kind == _K_FENCE:
                                tkey = (block_id, template_meta, wire_flags)
                            elif btype == "portal":
                                tkey = (block_id, wire_flags & 63)
                            else:
                                tkey = block_id
                                if btype == "grass_block":
                                    if snow_id and y < top_y and blocks[base + y + 1] == snow_id:
                                        tkey = (block_id, "snowy")
                                        template_meta = 1
                                    else:
                                        template_meta = 0
                            block_template = templates_by_id.get(tkey)
                            if block_template is None:
                                block_template = mesh_template(
                                    btype, meta=template_meta,
                                    door_mirror_right=door_mirror_right,
                                    chest_faces=chest_faces,
                                    wire_flags=wire_flags,
                                )
                                templates_by_id[tkey] = block_template

                        own_l_cache = None
                        own_l_components_cache = None
                        neighbor_ids = None
                        for direction, vertices, uvs, cull_backface, is_transparent, color in block_template:
                            dx, dy, dz = direction
                            direction_index = _FACE_NEIGHBOR_INDEX.get(direction, -1)
                            if direction_index == 6:
                                neighbor_id = block_id
                            elif direction_index >= 0:
                                if neighbor_ids is None:
                                    neighbor_ids = (
                                        xm[xm_base + y],
                                        xp[xp_base + y],
                                        blocks[base + y - 1] if y > 0 else 0,
                                        blocks[base + y + 1] if y < top_y else 0,
                                        zm[zm_base + y],
                                        zp[zp_base + y],
                                    )
                                neighbor_id = neighbor_ids[direction_index]
                            else:
                                neighbor_id = neighbor_id_fn(lx, y, lz, dx, dy, dz)
                            if is_fluid:
                                hidden = False
                            else:
                                hidden_index = (
                                    (block_id * 256 + neighbor_id) * 2
                                    + int(is_transparent)
                                )
                                hidden_state = hidden_cache[hidden_index]
                                if hidden_state:
                                    hidden = hidden_state == 2
                                else:
                                    hidden = is_hidden(
                                        n_cull, block_id,
                                        neighbor_id,
                                        defs_by_id,
                                        is_transparent,
                                    )
                                    hidden_cache[hidden_index] = 2 if hidden else 1
                                if hidden and definition_is_partial:
                                    hidden = quad_on_voxel_boundary(direction, vertices)
                            if hidden:
                                continue

                            if is_transparent:
                                target_extend = transparent_extend
                            elif definition_cutout_culled:
                                target_extend = cutout_culled_extend
                            elif not cull_backface:
                                target_extend = cutout_extend
                            else:
                                target_extend = opaque_extend

                            if own_l_cache is None:
                                own_l_components_cache = sample_light_components(lx, y, lz)
                                own_sky, own_block = own_l_components_cache
                                effective_own_sky = (
                                    own_sky if sky_sub is None
                                    else max(0, own_sky - sky_sub)
                                )
                                own_l_cache = min(15, max(effective_own_sky, own_block))
                            own_l = own_l_cache
                            if is_fluid:
                                if dy > 0:
                                    light_level = max(own_l, sample_light(lx, y + 1, lz))
                                else:
                                    light_level = max(
                                        sample_light(lx + dx, y + dy, lz + dz),
                                        sample_light(lx + dx, y + dy + 1, lz + dz),
                                    )
                            elif btype == "cactus":
                                light_level = max(
                                    own_l,
                                    sample_light(lx + dx, y + dy, lz + dz),
                                )
                                if dy == 0:
                                    light_level = max(
                                        light_level, sample_light(lx, y + 1, lz)
                                    )
                            elif n_cull == "none" or not cull_backface:
                                light_level = own_l
                            else:
                                ext_l = sample_light(lx + dx, y + dy, lz + dz)
                                light_level = own_l if own_l > ext_l else ext_l
                                if dy == 0:
                                    above = sample_light(lx, y + 1, lz)
                                    if above > light_level:
                                        light_level = above

                                if (dy > 0 and neighbor_id > 0
                                        and neighbor_id < len(id_to_type)
                                        and id_to_type[neighbor_id] == "cactus"):
                                    for ox, oz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                                        light_level = max(
                                            light_level,
                                            sample_light(lx + ox, y + 1, lz + oz),
                                        )
                                    light_level = max(
                                        light_level, sample_light(lx, y + 2, lz)
                                    )

                                if neighbor_id < len(partial_model_lut) and partial_model_lut[neighbor_id]:
                                    for sy in (-1, 1):
                                        light_level = max(
                                            light_level,
                                            sample_light(lx + dx, y + sy, lz + dz),
                                        )

                                if (definition_is_partial
                                        and not quad_on_voxel_boundary(direction, vertices)):
                                    for sx in (-1, 0, 1):
                                        for sy in (-1, 0, 1):
                                            for sz in (-1, 0, 1):
                                                if sx == 0 and sy == 0 and sz == 0:
                                                    continue
                                                light_level = max(
                                                    light_level,
                                                    sample_light(lx + sx, y + sy, lz + sz),
                                                )

                            color_key = (
                                int(direction[0]), int(direction[1]),
                                int(direction[2]), int(light_level),
                            )
                            face_rgb = face_color_cache.get(color_key)
                            if face_rgb is None:
                                face_rgb = _face_color(direction, light_level, hell=hell_lighting)
                                face_color_cache[color_key] = face_rgb
                            r, g, b = face_rgb
                            if btype == "redstone_wire":
                                r *= float(color[0])
                                g *= float(color[1])
                                b *= float(color[2])
                            tint = None
                            if biome_tints or btype == "leaves":
                                tint_pair = biome_tints.get((lx, lz)) if biome_tints else None
                                if btype == "grass_block" and direction == (0, 1, 0):
                                    tint = tint_pair[0] if tint_pair else None
                                elif btype == "leaves":
                                    biome_leaf_tint = tint_pair[1] if tint_pair else None
                                    tint = _leaves_tint(meta, biome_leaf_tint)
                                if tint is not None:
                                    r *= tint[0] / 255.0
                                    g *= tint[1] / 255.0
                                    b *= tint[2] / 255.0

                            vertex_colors = ((r, g, b),) * 4
                            if (smooth_lighting_enabled and not is_fluid
                                    and is_full_voxel_face(direction, vertices)):
                                vertex_colors = smooth_face_colors(
                                    direction, vertices, (lx, y, lz),
                                    sample_smooth_light,
                                    sample_smooth_ao_occluder,
                                    hell=hell_lighting,
                                )
                                tint_scale = (
                                    (tint[0] / 255.0, tint[1] / 255.0, tint[2] / 255.0)
                                    if tint is not None else (1.0, 1.0, 1.0)
                                )
                                vertex_colors = tuple(
                                    (
                                        rgb[0] * tint_scale[0],
                                        rgb[1] * tint_scale[1],
                                        rgb[2] * tint_scale[2],
                                    )
                                    for rgb in vertex_colors
                                )

                            v0, v1, v2, v3 = vertices
                            u0, u1, u2, u3 = uvs
                            face_vertices = (
                                (v0, u0), (v1, u1), (v2, u2),
                                (v0, u0), (v2, u2), (v3, u3),
                            )
                            for vertex_index, (vertex, uv) in zip(
                                (0, 1, 2, 0, 2, 3), face_vertices,
                            ):
                                vertex_r, vertex_g, vertex_b = vertex_colors[vertex_index]
                                target_extend((
                                    lx + vertex[0], y + vertex[1], lz + vertex[2],
                                    uv[0], uv[1], vertex_r, vertex_g, vertex_b,
                                ))

            scan_finished = time.perf_counter()
            mesh_scan_ms += (scan_finished - section_scan_start) * 1000.0
            pack_start = time.perf_counter()
            opaque_arr = array('f', opaque)
            cutout_arr = array('f', cutout)
            cutout_culled_arr = array('f', cutout_culled)
            transparent_arr = array('f', transparent)
            section["mesh_opaque"] = opaque_arr.tobytes()
            section["count_opaque"] = len(opaque_arr) // 8
            section["mesh_cutout"] = cutout_arr.tobytes()
            section["count_cutout"] = len(cutout_arr) // 8
            section["mesh_cutout_culled"] = cutout_culled_arr.tobytes()
            section["count_cutout_culled"] = len(cutout_culled_arr) // 8
            section["mesh_transparent"] = transparent_arr.tobytes()
            section["count_transparent"] = len(transparent_arr) // 8
            section["built"] = True
            self.dirty_sections.discard(section_idx)
            mesh_pack_ms += (time.perf_counter() - pack_start) * 1000.0

        self.dirty = bool(self.dirty_sections)
        if profiler is not None:
            profiler.record_ms("mesh.scan", mesh_scan_ms)
            profiler.record_ms("mesh.pack", mesh_pack_ms)

        if (all(section["built"] for section in self._sections)
                and (not self.dirty_sections or commit_partial)):
            merge_start = time.perf_counter()
            opaque_data = b"".join(section["mesh_opaque"] for section in self._sections)
            cutout_data = b"".join(section["mesh_cutout"] for section in self._sections)
            cutout_culled_data = b"".join(section["mesh_cutout_culled"] for section in self._sections)
            transparent_data = b"".join(section["mesh_transparent"] for section in self._sections)
            if profiler is not None:
                profiler.record_since("mesh.merge", merge_start)
            upload_start = time.perf_counter()
            self._vbo_opaque = self._upload_vbo(self._vbo_opaque, opaque_data)
            self._count_opaque = len(opaque_data) // _STRIDE
            self._vbo_cutout = self._upload_vbo(self._vbo_cutout, cutout_data)
            self._count_cutout = len(cutout_data) // _STRIDE
            self._vbo_cutout_culled = self._upload_vbo(self._vbo_cutout_culled, cutout_culled_data)
            self._count_cutout_culled = len(cutout_culled_data) // _STRIDE
            self._vbo_transparent = self._upload_vbo(self._vbo_transparent, transparent_data)
            self._count_transparent = len(transparent_data) // _STRIDE
            if profiler is not None:
                profiler.record_since("mesh.upload", upload_start)

            offsets = {
                "opaque": 0, "cutout": 0, "cutout_culled": 0,
                "transparent": 0,
            }
            for section in self._sections:
                for layer in offsets:
                    section[f"first_{layer}"] = offsets[layer]
                    offsets[layer] += section[f"count_{layer}"]
            self._has_mesh = True
        if profiler is not None:
            profiler.record_since("mesh.total", mesh_total_start)

    @staticmethod
    def _section_runs(visible_sections, section_count):
        indices = list(range(section_count)) if visible_sections is None else sorted({
            int(i) for i in visible_sections if 0 <= int(i) < section_count
        })
        if not indices:
            return []
        runs = []
        first = previous = indices[0]
        for current in indices[1:]:
            if current != previous + 1:
                runs.append((first, previous))
                first = current
            previous = current
        runs.append((first, previous))
        return runs

    def _draw_vbo_ranges(self, vbo, layer, visible_sections=None, *, shared_state=False):
        if vbo is None:
            return
        runs = self._section_runs(visible_sections, self.section_count)
        draw_ranges = []
        for first_section, last_section in runs:
            first_vertex = self._sections[first_section][f"first_{layer}"]
            last = self._sections[last_section]
            end_vertex = last[f"first_{layer}"] + last[f"count_{layer}"]
            count = end_vertex - first_vertex
            if count > 0:
                draw_ranges.append((first_vertex, count))
        if not draw_ranges:
            return 0

        glBindBuffer(GL_ARRAY_BUFFER, vbo)
        if not shared_state:
            glEnableClientState(GL_VERTEX_ARRAY)
            glEnableClientState(GL_TEXTURE_COORD_ARRAY)
            glEnableClientState(GL_COLOR_ARRAY)
        glVertexPointer(3, GL_FLOAT, _STRIDE, c_void_p(0))
        glTexCoordPointer(2, GL_FLOAT, _STRIDE, _UV_OFFSET)
        glColorPointer(3, GL_FLOAT, _STRIDE, _COLOR_OFFSET)
        for first_vertex, count in draw_ranges:
            glDrawArrays(GL_TRIANGLES, first_vertex, count)
        if not shared_state:
            glDisableClientState(GL_COLOR_ARRAY)
            glDisableClientState(GL_TEXTURE_COORD_ARRAY)
            glDisableClientState(GL_VERTEX_ARRAY)
            glBindBuffer(GL_ARRAY_BUFFER, 0)
        return len(draw_ranges)

    @staticmethod
    def draw_batch(chunk_sections, phase="all"):
        draw_solid = phase in ("all", "opaque")
        draw_translucent = phase in ("all", "transparent")
        entries = [
            (chunk, sections)
            for chunk, sections in chunk_sections
            if all(section["built"] for section in chunk._sections) and sections
        ]
        if not entries:
            return

        profiler = getattr(entries[0][0].world, "profiler", None)
        gpu_profiler = getattr(entries[0][0].world, "gpu_timer_profiler", None)
        texture = Block._get_texture()
        glActiveTexture(GL_TEXTURE0)
        glEnable(GL_TEXTURE_2D)
        texture.bind()
        glColor4f(1.0, 1.0, 1.0, 1.0)
        glEnableClientState(GL_VERTEX_ARRAY)
        glEnableClientState(GL_TEXTURE_COORD_ARRAY)
        glEnableClientState(GL_COLOR_ARRAY)

        def _draw_chunk_layer(chunk, sections, vbo, layer):
            if not vbo:
                return 0
            renderer = chunk.renderer
            if renderer is not None:
                ox, oy, oz = renderer.camera_relative_offset(
                    chunk.start_x, 0.0, chunk.start_z
                )
            else:
                ox, oy, oz = float(chunk.start_x), 0.0, float(chunk.start_z)
            glPushMatrix()
            glTranslatef(ox, oy, oz)
            draw_calls = chunk._draw_vbo_ranges(
                vbo, layer, sections, shared_state=True
            )
            glPopMatrix()
            return draw_calls

        if draw_solid:
            opaque_started = time.perf_counter()
            opaque_draw_calls = 0
            glDisable(GL_BLEND)
            glDepthMask(True)
            glEnable(GL_CULL_FACE)
            with (gpu_profiler.measure(
                    "gpu.terrain_opaque", {"visible_chunks": len(entries)}
                  ) if gpu_profiler is not None else nullcontext()):
                for chunk, sections in entries:
                    if chunk._count_opaque:
                        opaque_draw_calls += _draw_chunk_layer(
                            chunk, sections, chunk._vbo_opaque, "opaque"
                        )

            glDisable(GL_CULL_FACE)
            glAlphaFunc(GL_GREATER, 0.5)
            glEnable(GL_ALPHA_TEST)
            with (gpu_profiler.measure(
                    "gpu.terrain_cutout", {"visible_chunks": len(entries)}
                  ) if gpu_profiler is not None else nullcontext()):
                for chunk, sections in entries:
                    if chunk._count_cutout:
                        opaque_draw_calls += _draw_chunk_layer(
                            chunk, sections, chunk._vbo_cutout, "cutout"
                        )
            glDisable(GL_ALPHA_TEST)

            glEnable(GL_CULL_FACE)
            glAlphaFunc(GL_GREATER, 0.5)
            glEnable(GL_ALPHA_TEST)
            with (gpu_profiler.measure(
                    "gpu.terrain_cutout_culled", {"visible_chunks": len(entries)}
                  ) if gpu_profiler is not None else nullcontext()):
                for chunk, sections in entries:
                    if chunk._count_cutout_culled:
                        opaque_draw_calls += _draw_chunk_layer(
                            chunk, sections, chunk._vbo_cutout_culled, "cutout_culled"
                        )
            glDisable(GL_ALPHA_TEST)
            if profiler is not None:
                profiler.record_ms(
                    "render.opaque_draw",
                    (time.perf_counter() - opaque_started) * 1000.0,
                    event_details={
                        "chunks": len(entries),
                        "sections": sum(len(sections) for _, sections in entries),
                        "draw_calls": opaque_draw_calls,
                    },
                )

        if draw_translucent and any(chunk._count_transparent for chunk, _ in entries):
            collect_started = time.perf_counter()
            sources = []
            for chunk, sections in entries:
                for section_idx in sections:
                    section = chunk._sections[int(section_idx)]
                    data = section.get("mesh_transparent", b"")
                    if data:
                        sources.append((data, chunk.start_x, chunk.start_z))
            if profiler is not None:
                profiler.record_ms(
                    "render.transparent_collect",
                    (time.perf_counter() - collect_started) * 1000.0,
                    event_details={"mesh_sources": len(sources)},
                )

            renderer = next((chunk.renderer for chunk, _ in entries if chunk.renderer is not None), None)
            camera_pos = (
                float(getattr(renderer, "camX", 0.0)),
                float(getattr(renderer, "camY", 0.0)),
                float(getattr(renderer, "camZ", 0.0)),
            )
            camera_angles = (
                float(getattr(renderer, "pitch", 0.0)),
                float(getattr(renderer, "yaw", 0.0)),
                float(getattr(renderer, "roll", 0.0)),
            )
            sort_started = time.perf_counter()
            sorted_vertices, origin, cache_hit = Chunk._transparent_mesh_cache.get(
                sources,
                camera_pos,
                camera_angles,
                chunk_size=getattr(entries[0][0], "width", 16),
            )
            if profiler is not None:
                profiler.record_ms(
                    "render.transparent_sort",
                    (time.perf_counter() - sort_started) * 1000.0,
                    event_details={
                        "mesh_sources": len(sources),
                        "quads": len(sorted_vertices) // FLOATS_PER_QUAD,
                        "cache_hit": bool(cache_hit),
                    },
                )
            if len(sorted_vertices):
                needs_upload = not cache_hit or Chunk._sorted_transparent_vbo is None
                if Chunk._sorted_transparent_vbo is None:
                    Chunk._sorted_transparent_vbo = int(glGenBuffers(1))
                glEnable(GL_CULL_FACE)
                glEnable(GL_BLEND)
                glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
                glDepthMask(GL_FALSE)
                glBindBuffer(GL_ARRAY_BUFFER, Chunk._sorted_transparent_vbo)
                if needs_upload:
                    upload_started = time.perf_counter()
                    upload_bytes = int(sorted_vertices.nbytes)
                    with (gpu_profiler.measure(
                            "gpu.transparent_upload", {"bytes": upload_bytes}
                          ) if gpu_profiler is not None else nullcontext()):
                        glBufferData(GL_ARRAY_BUFFER, upload_bytes, None, GL_DYNAMIC_DRAW)
                        glBufferSubData(
                            GL_ARRAY_BUFFER, 0, upload_bytes, sorted_vertices,
                        )
                    if profiler is not None:
                        profiler.record_ms(
                            "render.transparent_upload",
                            (time.perf_counter() - upload_started) * 1000.0,
                            event_details={"bytes": upload_bytes},
                        )
                glVertexPointer(3, GL_FLOAT, _STRIDE, c_void_p(0))
                glTexCoordPointer(2, GL_FLOAT, _STRIDE, _UV_OFFSET)
                glColorPointer(3, GL_FLOAT, _STRIDE, _COLOR_OFFSET)
                glPushMatrix()
                glTranslatef(
                    float(origin[0]) - camera_pos[0],
                    -camera_pos[1],
                    float(origin[2]) - camera_pos[2],
                )
                draw_started = time.perf_counter()
                with (gpu_profiler.measure(
                        "gpu.transparent_draw",
                        {"quads": len(sorted_vertices) // FLOATS_PER_QUAD},
                      ) if gpu_profiler is not None else nullcontext()):
                    glDrawArrays(GL_TRIANGLES, 0, len(sorted_vertices) // 8)
                if profiler is not None:
                    profiler.record_ms(
                        "render.transparent_draw",
                        (time.perf_counter() - draw_started) * 1000.0,
                        event_details={
                            "quads": len(sorted_vertices) // FLOATS_PER_QUAD,
                            "cache_hit": bool(cache_hit),
                        },
                    )
                glPopMatrix()
                glDepthMask(GL_TRUE)
                glDisable(GL_BLEND)
                glEnable(GL_CULL_FACE)

        glEnable(GL_CULL_FACE)
        glBindBuffer(GL_ARRAY_BUFFER, 0)
        glDisableClientState(GL_COLOR_ARRAY)
        glDisableClientState(GL_TEXTURE_COORD_ARRAY)
        glDisableClientState(GL_VERTEX_ARRAY)
        texture.unbind()
        glDisable(GL_TEXTURE_2D)

    def draw(self, visible_sections=None):
        if getattr(self.world, "headless", False):
            return
        if not all(section["built"] for section in self._sections):
            return
        texture = Block._get_texture()
        glActiveTexture(GL_TEXTURE0)
        glEnable(GL_TEXTURE_2D)
        texture.bind()
        glColor4f(1.0, 1.0, 1.0, 1.0)

        renderer = self.renderer
        if renderer is not None:
            ox, oy, oz = renderer.camera_relative_offset(self.start_x, 0.0, self.start_z)
        else:
            ox, oy, oz = float(self.start_x), 0.0, float(self.start_z)
        glPushMatrix()
        glTranslatef(ox, oy, oz)

        if self._count_opaque:
            glEnable(GL_CULL_FACE)
            self._draw_vbo_ranges(self._vbo_opaque, "opaque", visible_sections)
        if self._count_cutout:
            glDisable(GL_CULL_FACE)
            self._draw_vbo_ranges(self._vbo_cutout, "cutout", visible_sections)
            glEnable(GL_CULL_FACE)
        if self._count_cutout_culled:
            glEnable(GL_CULL_FACE)
            glAlphaFunc(GL_GREATER, 0.5)
            glEnable(GL_ALPHA_TEST)
            self._draw_vbo_ranges(self._vbo_cutout_culled, "cutout_culled", visible_sections)
            glDisable(GL_ALPHA_TEST)

        glPopMatrix()
        texture.unbind()
        glDisable(GL_TEXTURE_2D)

    def draw_transparent(self, visible_sections=None):
        if getattr(self.world, "headless", False):
            return
        if not all(section["built"] for section in self._sections):
            return
        if self._count_transparent == 0:
            return
        texture = Block._get_texture()
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glAlphaFunc(GL_GREATER, 0.0)
        glEnable(GL_ALPHA_TEST)
        glDepthMask(GL_FALSE)
        glEnable(GL_TEXTURE_2D)
        glDisable(GL_CULL_FACE)
        texture.bind()
        glColor4f(1.0, 1.0, 1.0, 1.0)
        renderer = self.renderer
        if renderer is not None:
            ox, oy, oz = renderer.camera_relative_offset(self.start_x, 0.0, self.start_z)
        else:
            ox, oy, oz = float(self.start_x), 0.0, float(self.start_z)
        glPushMatrix()
        glTranslatef(ox, oy, oz)
        self._draw_vbo_ranges(self._vbo_transparent, "transparent", visible_sections)
        glPopMatrix()
        texture.unbind()
        glDisable(GL_TEXTURE_2D)
        glDepthMask(GL_TRUE)
        glDisable(GL_ALPHA_TEST)
        glDisable(GL_BLEND)
        glEnable(GL_CULL_FACE)

    def release(self):
        for attr in ("_vbo_opaque", "_vbo_cutout", "_vbo_cutout_culled", "_vbo_transparent"):
            vbo = getattr(self, attr)
            if vbo is not None:
                if not getattr(self.world, "headless", False):
                    glDeleteBuffers(1, [vbo])
                setattr(self, attr, None)
        self._count_opaque = 0
        self._count_cutout = 0
        self._count_cutout_culled = 0
        self._count_transparent = 0
        for section in self._sections:
            section["mesh_opaque"] = b""
            section["mesh_cutout"] = b""
            section["mesh_cutout_culled"] = b""
            section["mesh_transparent"] = b""
            section["count_opaque"] = 0
            section["count_cutout"] = 0
            section["count_cutout_culled"] = 0
            section["count_transparent"] = 0
            section["built"] = False
