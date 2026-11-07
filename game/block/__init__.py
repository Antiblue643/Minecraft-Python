import json
import math
from OpenGL.GL import GL_CLAMP
from engine.textures import Texture
from engine.lighting import default_lighting
from game.block.material import material_for_block, step_sound_for_block


class Block:
    _block_defs = None
    _model_defs = None
    _texture_atlas = "game/textures/terrain.png"
    _shared_texture = None
    _fancy_graphics = True
    _LEAF_FANCY_FACE = 52
    _LEAF_FAST_FACE = 53

    @classmethod
    def set_fancy_graphics(cls, fancy: bool):
        cls._fancy_graphics = bool(fancy)
        cls._load_defs()
        leaves = cls._block_defs.get("leaves")
        if leaves is not None:
            if cls._fancy_graphics:
                leaves["nCull"] = "none"
                leaves["faces"] = cls._LEAF_FANCY_FACE
            else:
                leaves["nCull"] = "all"
                leaves["faces"] = cls._LEAF_FAST_FACE
        try:
            from game.world.chunk import Chunk
            dead = [k for k in Chunk._mesh_template_cache
                    if k == "leaves" or (isinstance(k, tuple) and k and k[0] == "leaves")]
            for k in dead:
                Chunk._mesh_template_cache.pop(k, None)
        except Exception:
            pass

    _CUBE_FACES = (
        ("face_front", (0, 0, 1),
         ((-0.5, -0.5, 0.5), (0.5, -0.5, 0.5), (0.5, 0.5, 0.5), (-0.5, 0.5, 0.5)), True),
        ("face_back", (0, 0, -1),
         ((0.5, -0.5, -0.5), (-0.5, -0.5, -0.5), (-0.5, 0.5, -0.5), (0.5, 0.5, -0.5)), True),
        ("face_top", (0, 1, 0),
         ((-0.5, 0.5, 0.5), (0.5, 0.5, 0.5), (0.5, 0.5, -0.5), (-0.5, 0.5, -0.5)), True),
        ("face_bottom", (0, -1, 0),
         ((-0.5, -0.5, -0.5), (0.5, -0.5, -0.5), (0.5, -0.5, 0.5), (-0.5, -0.5, 0.5)), True),
        ("face_right", (1, 0, 0),
         ((0.5, -0.5, 0.5), (0.5, -0.5, -0.5), (0.5, 0.5, -0.5), (0.5, 0.5, 0.5)), True),
        ("face_left", (-1, 0, 0),
         ((-0.5, -0.5, -0.5), (-0.5, -0.5, 0.5), (-0.5, 0.5, 0.5), (-0.5, 0.5, -0.5)), True),
    )

    _FACE_MAP = {
        "face_right": 0, "face_left": 1, "face_bottom": 2,
        "face_top": 3, "face_front": 4, "face_back": 5,
    }

    @classmethod
    def _load_defs(cls):
        if cls._block_defs is None:
            from game.content_data import load_block_definitions
            cls._block_defs = load_block_definitions()
        if cls._model_defs is None:
            with open("game/data/model.json") as f:
                cls._model_defs = json.load(f)["models"]

    @classmethod
    def _get_texture(cls):
        if cls._shared_texture is None:
            cls._shared_texture = Texture(cls._texture_atlas, repeat_mode=GL_CLAMP)
        return cls._shared_texture

    def __init__(self, block_type, position, world=None, renderer=None):
        Block._load_defs()

        self.block_type = block_type
        self.pos = position
        self.world = world
        self.renderer = renderer

        self.definition = Block._block_defs.get(block_type, {})
        self.material = material_for_block(block_type, self.definition)
        self.step_sound = step_sound_for_block(block_type, self.definition)

        self.faces = self.definition.get("faces", 0)
        self.model_name = self.definition.get("model", "cube")
        self.intangible = self.definition.get("intangible", False)
        self.nCull = self.definition.get("nCull", "all")
        self.transparent = self.definition.get("transparent", False)

        self.model = Block._model_defs.get(self.model_name, {})

        self._display_list = None

        self.bounds = self.definition.get("bounds", [0.0, 0.0, 0.0, 1.0, 1.0, 1.0])
        self.collision = self.definition.get(
            "collision", not self.intangible
        )

    @staticmethod
    def classic_to_center(bounds):
        return (
            bounds[0] - 0.5, bounds[1] - 0.5, bounds[2] - 0.5,
            bounds[3] - 0.5, bounds[4] - 0.5, bounds[5] - 0.5,
        )

    def _resolve_meta(self):
        meta = getattr(self, "_mesh_meta", None)
        if meta is None and self.world is not None:
            try:
                meta = self.world.get_metadata(
                    int(self.pos[0]), int(self.pos[1]), int(self.pos[2])
                )
            except Exception:
                meta = 0
        return int(meta or 0) & 15

    def _oriented_bounds(self):
        meta = self._resolve_meta()
        bt = self.block_type
        door_is_upper = bt in ("wooden_door", "iron_door") and bool(meta & 8)
        door_y = int(self.pos[1]) - 1 if door_is_upper and self.pos is not None else (
            int(self.pos[1]) if bt in ("wooden_door", "iron_door") and self.pos is not None else None
        )
        if bt in ("wooden_door", "iron_door") and door_is_upper and self.world is not None and self.pos is not None:
            try:
                x, y, z = (int(self.pos[0]), int(self.pos[1]), int(self.pos[2]))
                lower_type = self.world.get_block_type(x, y - 1, z)
                if lower_type == bt:
                    meta = self.world.get_metadata(x, y - 1, z) & 7
            except Exception:
                pass
        if bt == "cake":
            return [(1.0 + (meta % 6) * 2.0) / 16.0, 0.0, 1.0 / 16.0,
                    15.0 / 16.0, 8.0 / 16.0, 15.0 / 16.0]
        if bt == "rail":
            return [0.0, 0.0, 0.0, 1.0, 10.0 / 16.0 if 2 <= meta <= 5 else 2.0 / 16.0, 1.0]
        if bt == "lever":
            facing = meta & 7
            if facing == 1:
                return [0, 0.2, 5/16, 6/16, 0.8, 11/16]
            if facing == 2:
                return [10/16, 0.2, 5/16, 1, 0.8, 11/16]
            if facing == 3:
                return [5/16, 0.2, 0, 11/16, 0.8, 6/16]
            if facing == 4:
                return [5/16, 0.2, 10/16, 11/16, 0.8, 1]
            return [4/16, 0, 4/16, 12/16, 0.6, 12/16]
        if bt == "stone_button":
            facing = meta & 7
            thickness = (1 if meta & 8 else 2) / 16
            if facing == 1:
                return [0, 6/16, 5/16, thickness, 10/16, 11/16]
            if facing == 2:
                return [1-thickness, 6/16, 5/16, 1, 10/16, 11/16]
            if facing == 3:
                return [5/16, 6/16, 0, 11/16, 10/16, thickness]
            return [5/16, 6/16, 1-thickness, 11/16, 10/16, 1]
        if bt in ("stone_pressure_plate", "wooden_pressure_plate"):
            h = 1/32 if (meta & 1) else 1/16
            return [1/16, 0, 1/16, 15/16, h, 15/16]
        if bt in ("torch", "redstone_torch", "unlit_redstone_torch"):
            meta &= 7
            h = 0.1
            if meta == 1:
                return [0.0, 0.2, 0.5 - h, h * 2, 0.8, 0.5 + h]
            if meta == 2:
                return [1.0 - h * 2, 0.2, 0.5 - h, 1.0, 0.8, 0.5 + h]
            if meta == 3:
                return [0.5 - h, 0.2, 0.0, 0.5 + h, 0.8, h * 2]
            if meta == 4:
                return [0.5 - h, 0.2, 1.0 - h * 2, 0.5 + h, 0.8, 1.0]
            return [0.5 - h, 0.0, 0.5 - h, 0.5 + h, 0.6, 0.5 + h]
        if bt == "fire":
            boxes = self._fire_selection_bounds()
            return list(boxes[0]) if boxes else [0.0, 0.0, 0.0, 1.0, 1.4, 1.0]
        if bt == "portal":
            x, y, z = (int(value) for value in self.pos)
            has_x_neighbor = self.world is not None and (
                self.world.get_block_type(x - 1, y, z) == "portal"
                or self.world.get_block_type(x + 1, y, z) == "portal"
            )
            thickness = 2.0 / 16.0
            if has_x_neighbor:
                return [0.0, 0.0, 0.5 - thickness, 1.0, 1.0, 0.5 + thickness]
            return [0.5 - thickness, 0.0, 0.0, 0.5 + thickness, 1.0, 1.0]
        if bt in ("wooden_door", "iron_door"):
            meta = int(meta) & 7
            closed_state = (meta - 1) & 3
            is_open = bool(meta & 4)
            state = (meta & 3) if is_open else closed_state
            if is_open and self._door_is_right_leaf(closed_state, door_y):
                state = (closed_state - 1) & 3
            t = 3.0 / 16.0
            if state == 0:
                return [0.0, 0.0, 0.0, 1.0, 1.0, t]
            if state == 1:
                return [1.0 - t, 0.0, 0.0, 1.0, 1.0, 1.0]
            if state == 2:
                return [0.0, 0.0, 1.0 - t, 1.0, 1.0, 1.0]
            return [0.0, 0.0, 0.0, t, 1.0, 1.0]
        if bt == "ladder":
            t = 2.0 / 16.0
            if meta == 2:
                return [0.0, 0.0, 1.0 - t, 1.0, 1.0, 1.0]
            if meta == 3:
                return [0.0, 0.0, 0.0, 1.0, 1.0, t]
            if meta == 4:
                return [1.0 - t, 0.0, 0.0, 1.0, 1.0, 1.0]
            if meta == 5:
                return [0.0, 0.0, 0.0, t, 1.0, 1.0]
            return [0.0, 0.0, 0.0, 1.0, 1.0, t]
        return list(self.bounds)

    def _door_is_right_leaf(self, closed_state, door_y=None):
        if self.world is None or self.pos is None:
            return bool(getattr(self, "_door_mirror_right", False))
        x, block_y, z = (int(self.pos[0]), int(self.pos[1]), int(self.pos[2]))
        y = int(block_y if door_y is None else door_y)
        if hasattr(self.world, "is_door_mirrored"):
            return self.world.is_door_mirrored(x, y, z)
        hinges = getattr(self.world, "door_hinges", {})
        return bool(hinges.get((x, y, z), False))

    def _fire_selection_bounds(self):
        return [(0.0, 0.0, 0.0, 1.0, 1.0, 1.0)]

    def get_selection_bounds(self):
        return Block.classic_to_center(self._oriented_bounds())

    def get_collision_bounds(self):
        if self.collision is False:
            return None
        if isinstance(self.collision, (list, tuple)):
            return Block.classic_to_center(self.collision)
        return Block.classic_to_center(self._oriented_bounds())

    @staticmethod
    def _stair_step_bounds(facing, y0, y1):
        if facing == 0:
            return [0.5, y0, 0.0, 1.0, y1, 1.0]
        if facing == 1:
            return [0.0, y0, 0.0, 0.5, y1, 1.0]
        if facing == 2:
            return [0.0, y0, 0.5, 1.0, y1, 1.0]
        return [0.0, y0, 0.0, 1.0, y1, 0.5]

    def get_collision_boxes(self):
        if self.collision is False:
            return []
        if self.block_type == "farmland":
            return [(-0.5, -0.5, -0.5, 0.5, 0.5, 0.5)]
        if self.block_type == "cake":
            x0, _y0, z0, x1, _y1, z1 = self._oriented_bounds()
            return [Block.classic_to_center((x0, 0.0, z0, x1, 7.0 / 16.0, z1))]
        if isinstance(self.collision, (list, tuple)):
            return [Block.classic_to_center(self.collision)]
        if self.model_name == "stairs" or self.block_type in ("stairs", "cobblestone_stairs"):
            meta = self._resolve_meta()
            facing, upside_down = meta & 3, bool(meta & 4)
            if upside_down:
                boxes = [[0.0, 0.5, 0.0, 1.0, 1.0, 1.0], self._stair_step_bounds(facing, 0.0, 0.5)]
            else:
                boxes = [[0.0, 0.0, 0.0, 1.0, 0.5, 1.0], self._stair_step_bounds(facing, 0.5, 1.0)]
            return [Block.classic_to_center(box) for box in boxes]
        return [Block.classic_to_center(self._oriented_bounds())]

    def get_selection_boxes(self):
        if self.block_type == "fire":
            return [Block.classic_to_center(box) for box in self._fire_selection_bounds()]
        if self.block_type in ("sign", "wall_sign"):
            from game.rendering.sign_selection import sign_selection_boxes
            return sign_selection_boxes(
                self._resolve_meta(), self.bounds,
                wall=self.block_type == "wall_sign",
            )
        if self.model_name == "stairs" or self.block_type in ("stairs", "cobblestone_stairs"):
            return self.get_collision_boxes()
        return [self.get_selection_bounds()]

    def get_destroy_boxes(self):
        if self.model_name == "fence" or self.block_type == "fence":
            return self._fence_destroy_boxes()
        if self.block_type in ("sign", "wall_sign"):
            from game.rendering.sign_selection import sign_destroy_boxes
            return sign_destroy_boxes(
                self._resolve_meta(), wall=self.block_type == "wall_sign",
            )
        return self.get_selection_boxes()

    def _fence_destroy_boxes(self):
        boxes = [(-2.0 / 16.0, -0.5, -2.0 / 16.0, 2.0 / 16.0, 0.5, 2.0 / 16.0)]
        flags = self._fence_connection_flags()
        if not flags:
            return boxes
        bar_ys = (
            (-0.5 + 6.0 / 16.0, -0.5 + 9.0 / 16.0),
            (-0.5 + 12.0 / 16.0, -0.5 + 15.0 / 16.0),
        )
        slim0, slim1 = -1.0 / 16.0, 1.0 / 16.0
        neg_x = bool(flags & 1)
        pos_x = bool(flags & 2)
        neg_z = bool(flags & 4)
        pos_z = bool(flags & 8)
        if neg_x or pos_x:
            x0 = -0.5 if neg_x else slim0
            x1 = 0.5 if pos_x else slim1
            for y0, y1 in bar_ys:
                boxes.append((x0, y0, slim0, x1, y1, slim1))
        if neg_z or pos_z:
            z0 = -0.5 if neg_z else slim0
            z1 = 0.5 if pos_z else slim1
            for y0, y1 in bar_ys:
                boxes.append((slim0, y0, z0, slim1, y1, z1))
        return boxes


    def _should_cull(self, direction, face_name=""):
        if not self.world or self.nCull == "none":
            return False

        nx = self.pos[0] + direction[0]
        ny = self.pos[1] + direction[1]
        nz = self.pos[2] + direction[2]

        if hasattr(self.world, "get_block_type"):
            neighbor_type = self.world.get_block_type(nx, ny, nz)
            if neighbor_type is None:
                return False
            neighbor_def = Block._block_defs.get(neighbor_type, {})
            neighbor_n_cull = neighbor_def.get("nCull", "all")
        else:
            neighbor = self.world.get_block(nx, ny, nz)
            if neighbor is None:
                return False
            neighbor_type = neighbor.block_type
            neighbor_n_cull = neighbor.nCull

        if self.nCull == "all":
            return neighbor_n_cull == "all"

        if self.nCull == "type":
            return neighbor_type == self.block_type

        if self.nCull == "liquid":
            return True

        return False

    def _atlas_uv(self, atlas_index):
        cols = 16
        row = atlas_index // cols
        col = atlas_index % cols

        u = col * 0.0625
        v = 1 - (row + 1) * 0.0625

        return u, v

    def _face_uvs_for_quad(self, quad, atlas_index, direction):
        u0, v0 = self._atlas_uv(atlas_index)
        tile = 1.0 / 16.0
        dx, dy, dz = direction
        result = []
        for x, y, z in quad:
            xf, yf, zf = x + 0.5, y + 0.5, z + 0.5
            if dx == 1:
                uf, vf = 1.0 - zf, yf
            elif dx == -1:
                uf, vf = zf, yf
            elif dz == 1:
                uf, vf = xf, yf
            elif dz == -1:
                uf, vf = 1.0 - xf, yf
            elif dy == 1:
                uf, vf = xf, 1.0 - zf
            else:
                uf, vf = xf, zf
            result.append((u0 + uf * tile, v0 + vf * tile))
        return tuple(result)

    def get_mesh_data(self):
        if self.model_name == "snow_layer" or self.block_type == "snow":
            return self._get_snow_layer_mesh_data()
        if self.model_name == "cactus" or self.block_type == "cactus":
            return self._get_cactus_mesh_data()
        if self.block_type == "cake":
            return self._get_cake_mesh_data()
        if self.model_name == "cube":
            return self._get_cube_mesh_data()
        if self.model_name == "farmland" or self.block_type == "farmland":
            return self._get_farmland_mesh_data()
        if self.model_name == "fire" or self.block_type == "fire":
            return self._get_fire_mesh_data()
        if self.model_name == "portal" or self.block_type == "portal":
            return self._get_portal_mesh_data()
        if self.model_name == "redstone_wire" or self.block_type == "redstone_wire":
            return self._get_redstone_wire_mesh_data()
        if self.model_name == "lever" or self.block_type == "lever":
            return self._get_lever_mesh_data()
        if self.model_name == "button" or self.block_type == "stone_button":
            return self._get_button_mesh_data()
        if self.model_name == "pressure_plate" or self.block_type in (
            "stone_pressure_plate", "wooden_pressure_plate",
        ):
            return self._get_pressure_plate_mesh_data()
        if self.model_name == "crops" or self.block_type == "crops":
            return self._get_crops_mesh_data()
        if self.model_name == "torch" or self.block_type == "torch":
            return self._get_torch_mesh_data()
        if self.model_name == "slab" or self.block_type == "stone_slab":
            return self._get_slab_mesh_data()
        if self.model_name == "stairs" or self.block_type in ("stairs", "cobblestone_stairs"):
            return self._get_stairs_mesh_data()
        if self.model_name == "door" or self.block_type == "wooden_door":
            return self._get_door_mesh_data()
        if self.model_name == "ladder" or self.block_type == "ladder":
            return self._get_ladder_mesh_data()
        if self.model_name == "rail" or self.block_type == "rail":
            return self._get_rail_mesh_data()
        if self.model_name == "fence" or self.block_type == "fence":
            return self._get_fence_mesh_data()
        if self.model_name == "sign" or self.block_type == "sign":
            return []
        return self._get_generic_mesh_data()

    def _get_redstone_wire_mesh_data(self):
        from game.block.redstone_wire import build_redstone_wire_mesh_data

        meta = self._resolve_meta()
        texture = 84 + (16 if meta > 0 else 0)
        base_shade = default_lighting.face_color((0, 1, 0))
        flags = int(getattr(self, "_mesh_wire_flags", 0))
        if not flags and self.world is not None:
            try:
                from game.block.redstone_wire import redstone_wire_render_flags_from_world
                flags = redstone_wire_render_flags_from_world(self.world, self.pos)
            except Exception:
                flags = 0
        return build_redstone_wire_mesh_data(
            self.pos,
            flags=flags,
            texture_index=texture,
            color=tuple(float(c) for c in base_shade),
        )

    def _box_mesh_local(self, bounds, texture_index, *, double_sided=False, scale_uv=False):
        x0, y0, z0, x1, y1, z1 = (float(v) for v in bounds)
        px, py, pz = (float(v) for v in self.pos)
        quads = (
            ((0, 0, 1), ((x0,y0,z1),(x1,y0,z1),(x1,y1,z1),(x0,y1,z1))),
            ((0, 0,-1), ((x1,y0,z0),(x0,y0,z0),(x0,y1,z0),(x1,y1,z0))),
            ((0, 1, 0), ((x0,y1,z1),(x1,y1,z1),(x1,y1,z0),(x0,y1,z0))),
            ((0,-1, 0), ((x0,y0,z0),(x1,y0,z0),(x1,y0,z1),(x0,y0,z1))),
            ((1, 0, 0), ((x1,y0,z1),(x1,y0,z0),(x1,y1,z0),(x1,y1,z1))),
            ((-1,0, 0), ((x0,y0,z0),(x0,y0,z1),(x0,y1,z1),(x0,y1,z0))),
        )
        out = []
        for direction, quad in quads:
            vertices = [(px+x, py+y, pz+z) for x,y,z in quad]
            if scale_uv:
                uvs = self._face_uvs_for_quad(quad, int(texture_index), direction)
            else:
                u, v = self._atlas_uv(int(texture_index))
                tile = 1.0 / 16.0
                u0, u1 = u, u + tile * (15.99 / 16.0)
                v0, v1 = v, v + tile * (15.99 / 16.0)
                uvs = ((u0,v1),(u1,v1),(u1,v0),(u0,v0))
            color = default_lighting.face_color(direction)
            out.append((direction, vertices, uvs, False, False, color))
            if double_sided:
                out.append((tuple(-v for v in direction), list(reversed(vertices)),
                            tuple(reversed(uvs)), False, False, color))
        return out


    def _fence_connection_flags(self):
        flags = int(getattr(self, "_mesh_fence_flags", 0))
        if flags:
            return flags
        world = self.world
        if world is None or self.pos is None:
            return 0
        x, y, z = (int(v) for v in self.pos)
        get = getattr(world, "get_block_type", None)
        if not callable(get):
            return 0
        if get(x - 1, y, z) == "fence":
            flags |= 1
        if get(x + 1, y, z) == "fence":
            flags |= 2
        if get(x, y, z - 1) == "fence":
            flags |= 4
        if get(x, y, z + 1) == "fence":
            flags |= 8
        return flags

    def _get_fence_mesh_data(self):
        faces = self.faces
        texture = int(faces[0] if isinstance(faces, (list, tuple)) else faces)
        flags = self._fence_connection_flags()
        out = []
        post = (-2.0 / 16.0, -0.5, -2.0 / 16.0, 2.0 / 16.0, 0.5, 2.0 / 16.0)
        out.extend(self._box_mesh_local(post, texture, scale_uv=True))
        if not flags:
            return out
        bar_ys = (
            (-0.5 + 6.0 / 16.0, -0.5 + 9.0 / 16.0),
            (-0.5 + 12.0 / 16.0, -0.5 + 15.0 / 16.0),
        )
        slim0, slim1 = -1.0 / 16.0, 1.0 / 16.0
        neg_x = bool(flags & 1)
        pos_x = bool(flags & 2)
        neg_z = bool(flags & 4)
        pos_z = bool(flags & 8)
        if neg_x or pos_x:
            x0 = -0.5 if neg_x else slim0
            x1 = 0.5 if pos_x else slim1
            for y0, y1 in bar_ys:
                out.extend(self._box_mesh_local(
                    (x0, y0, slim0, x1, y1, slim1), texture, scale_uv=True,
                ))
        if neg_z or pos_z:
            z0 = -0.5 if neg_z else slim0
            z1 = 0.5 if pos_z else slim1
            for y0, y1 in bar_ys:
                out.extend(self._box_mesh_local(
                    (slim0, y0, z0, slim1, y1, z1), texture, scale_uv=True,
                ))
        return out

    def _get_cactus_mesh_data(self):
        px, py, pz = (float(v) for v in self.pos)
        faces_def = self.faces
        is_list = isinstance(faces_def, (list, tuple))
        inset = 1.0 / 16.0
        out = []
        for name, direction, local_quad, cull_backface in self._CUBE_FACES:
            if self._should_cull(direction, name):
                continue

            dx, dy, dz = direction
            vertices_local = []
            for x, y, z in local_quad:
                if dx:
                    x = dx * (0.5 - inset)
                elif dz:
                    z = dz * (0.5 - inset)
                vertices_local.append((x, y, z))
            vertices = [(px + x, py + y, pz + z) for x, y, z in vertices_local]

            atlas_index = (
                faces_def[self._FACE_MAP.get(name, 0)]
                if is_list else faces_def
            )
            raw_uvs = self._face_uvs_for_quad(local_quad, int(atlas_index), direction)
            u0, v0 = self._atlas_uv(int(atlas_index))
            edge = 0.01 / 256.0
            tile = 1.0 / 16.0
            uvs = tuple(
                (u0 + edge + ((u - u0) / tile) * (tile - 2.0 * edge),
                 v0 + edge + ((v - v0) / tile) * (tile - 2.0 * edge))
                for u, v in raw_uvs
            )

            out.append((
                direction, vertices, uvs, cull_backface, bool(self.transparent),
                default_lighting.face_color(direction),
            ))
        return out

    def _get_snow_layer_mesh_data(self):
        return self._box_mesh_local(
            (-0.5, -0.5, -0.5, 0.5, -0.5 + 2.0 / 16.0, 0.5), 66,
            scale_uv=True,
        )

    def _get_portal_mesh_data(self):
        x, y, z = (int(value) for value in self.pos)
        neighbors = getattr(self, "_mesh_portal_neighbors", None)
        if neighbors is None:
            neighbors = (False, False, False, False, False, False)
            if self.world is not None:
                neighbors = (
                    self.world.get_block_type(x - 1, y, z) == "portal",
                    self.world.get_block_type(x + 1, y, z) == "portal",
                    self.world.get_block_type(x, y, z - 1) == "portal",
                    self.world.get_block_type(x, y, z + 1) == "portal",
                    self.world.get_block_type(x, y - 1, z) == "portal",
                    self.world.get_block_type(x, y + 1, z) == "portal",
                )
        x_minus, x_plus, z_minus, z_plus, y_minus, y_plus = neighbors
        bounds = (
            (-0.5, -0.5, -2 / 16, 0.5, 0.5, 2 / 16)
            if x_minus or x_plus else
            (-2 / 16, -0.5, -0.5, 2 / 16, 0.5, 0.5)
        )
        return [
            (direction, vertices, uvs, cull_backface, True, color)
            for direction, vertices, uvs, cull_backface, _transparent, color
            in self._box_mesh_local(bounds, 14, scale_uv=True)
            if not (
                (direction == (0, -1, 0) and y_minus)
                or (direction == (0, 1, 0) and y_plus)
                or (direction == (-1, 0, 0) and x_minus)
                or (direction == (1, 0, 0) and x_plus)
                or (
                    not (x_minus or x_plus)
                    and direction == (0, 0, -1) and z_minus
                )
                or (
                    not (x_minus or x_plus)
                    and direction == (0, 0, 1) and z_plus
                )
            )
        ]

    def _get_pressure_plate_mesh_data(self):
        meta = self._resolve_meta()
        height = 0.03125 if (meta & 1) else 0.0625
        tile = 1 if self.block_type == "stone_pressure_plate" else 4
        return self._box_mesh_local(
            (-7/16, -0.5, -7/16, 7/16, -0.5 + height, 7/16), tile,
            scale_uv=True,
        )

    def _get_button_mesh_data(self):
        meta = self._resolve_meta()
        return self._box_mesh_local(self.classic_to_center(self._oriented_bounds()), 1,
                                    scale_uv=True)

    def _get_lever_mesh_data(self):
        import math

        px, py, pz = (float(v) for v in self.pos)
        meta = self._resolve_meta()
        facing = meta & 7
        powered = bool(meta & 8)
        lever_span_low, lever_span_high = 1/2 - 1/4, 1/2 + 1/4
        base_half_width = 3/16
        base_thickness = 3/16
        wall_y_low, wall_y_high = lever_span_low, lever_span_high
        if facing == 1:
            render_bounds = [0, wall_y_low, 1/2-base_half_width, base_thickness, wall_y_high, 1/2+base_half_width]
        elif facing == 2:
            render_bounds = [1-base_thickness, wall_y_low, 1/2-base_half_width, 1, wall_y_high, 1/2+base_half_width]
        elif facing == 3:
            render_bounds = [1/2-base_half_width, wall_y_low, 0, 1/2+base_half_width, wall_y_high, base_thickness]
        elif facing == 4:
            render_bounds = [1/2-base_half_width, wall_y_low, 1-base_thickness, 1/2+base_half_width, wall_y_high, 1]
        elif facing == 5:
            render_bounds = [1/2-base_half_width, 0, lever_span_low, 1/2+base_half_width, base_thickness, lever_span_high]
        else:
            render_bounds = [lever_span_low, 0, 1/2-base_half_width, lever_span_high, base_thickness, 1/2+base_half_width]
        out = self._box_mesh_local(self.classic_to_center(render_bounds), 16,
                                   scale_uv=True)

        v = [(-1/16, 0, -1/16), (1/16, 0, -1/16),
             (1/16, 0, 1/16), (-1/16, 0, 1/16),
             (-1/16, 10/16, -1/16), (1/16, 10/16, -1/16),
             (1/16, 10/16, 1/16), (-1/16, 10/16, 1/16)]
        theta_x = (2 * math.pi / 9) if powered else -(2 * math.pi / 9)
        rx = -1/16 if powered else 1/16
        ca, sa = math.cos(theta_x), math.sin(theta_x)
        rot_y = {1: -math.pi/2, 2: math.pi/2, 3: math.pi, 4: 0.0}.get(facing, 0.0)
        cy, sy = math.cos(rot_y), math.sin(rot_y)
        transformed = []
        for x, y, z in v:
            z += rx
            y, z = y * ca + z * sa, z * ca - y * sa
            if facing == 6:
                x, z = x * 0.0 + z, z * 0.0 - x
            if facing < 5:
                y -= 3/8
                y, z = y * math.cos(math.pi/2) + z * math.sin(math.pi/2), z * math.cos(math.pi/2) - y * math.sin(math.pi/2)
                x, z = x * cy + z * sy, -x * sy + z * cy
            else:
                y -= 3/8
            transformed.append((px+x, py+y, pz+z))

        face_indices = ((0,1,2,3),(7,6,5,4),(1,0,4,5),(2,1,5,6),(3,2,6,7),(0,3,7,4))
        u0, v0 = self._atlas_uv(96)
        small_uv = ((u0+7/256,v0+8/256),(u0+9/256,v0+8/256),
                    (u0+9/256,v0+10/256),(u0+7/256,v0+10/256))
        long_uv = ((u0+7/256,v0),(u0+9/256,v0),
                   (u0+9/256,v0+10/256),(u0+7/256,v0+10/256))
        normals = ((0,-1,0),(0,1,0),(0,0,-1),(1,0,0),(0,0,1),(-1,0,0))
        for face_i, indices in enumerate(face_indices):
            direction = normals[face_i]
            out.append((direction, [transformed[i] for i in indices],
                        small_uv if face_i < 2 else long_uv,
                        False, False, default_lighting.face_color(direction)))
        return out

    def _get_farmland_mesh_data(self):
        px, py, pz = self.pos
        all_verts = []
        tile = 0.0625
        top_y = 0.4375
        bot_y = -0.5
        faces_def = self.faces
        meta = getattr(self, "_mesh_meta", None)
        if meta is None and self.world is not None:
            try:
                meta = self.world.get_metadata(int(self.pos[0]), int(self.pos[1]), int(self.pos[2]))
            except Exception:
                meta = 0
        meta = int(meta or 0)
        if isinstance(faces_def, list):
            side_idx = faces_def[0]
            top_idx = faces_def[3] if len(faces_def) > 3 else 87
            if meta > 0:
                top_idx = top_idx - 1  
            bot_idx = faces_def[2] if len(faces_def) > 2 else 2
        else:
            side_idx = top_idx = bot_idx = faces_def

        def _uv(idx):
            u, v = self._atlas_uv(idx)
            return ((u, v), (u + tile, v), (u + tile, v + tile), (u, v + tile))

        uvs = _uv(top_idx)
        top_quad = (
            (-0.5, top_y, 0.5), (0.5, top_y, 0.5),
            (0.5, top_y, -0.5), (-0.5, top_y, -0.5),
        )
        all_verts.append((
            (0, 1, 0),
            [(px + x, py + y, pz + z) for x, y, z in top_quad],
            uvs, True, False, default_lighting.face_color((0, 1, 0)),
        ))
        uvs = _uv(bot_idx)
        bot_quad = (
            (-0.5, bot_y, -0.5), (0.5, bot_y, -0.5),
            (0.5, bot_y, 0.5), (-0.5, bot_y, 0.5),
        )
        all_verts.append((
            (0, -1, 0),
            [(px + x, py + y, pz + z) for x, y, z in bot_quad],
            uvs, True, False, default_lighting.face_color((0, -1, 0)),
        ))
        side_faces = (
            ((0, 0, 1),  ((-0.5, bot_y, 0.5), (0.5, bot_y, 0.5), (0.5, top_y, 0.5), (-0.5, top_y, 0.5))),
            ((0, 0, -1), ((0.5, bot_y, -0.5), (-0.5, bot_y, -0.5), (-0.5, top_y, -0.5), (0.5, top_y, -0.5))),
            ((1, 0, 0),  ((0.5, bot_y, 0.5), (0.5, bot_y, -0.5), (0.5, top_y, -0.5), (0.5, top_y, 0.5))),
            ((-1, 0, 0), ((-0.5, bot_y, -0.5), (-0.5, bot_y, 0.5), (-0.5, top_y, 0.5), (-0.5, top_y, -0.5))),
        )
        uvs = _uv(side_idx)
        for direction, quad in side_faces:
            if self._should_cull(direction):
                continue
            all_verts.append((
                direction,
                [(px + x, py + y, pz + z) for x, y, z in quad],
                uvs, True, False, default_lighting.face_color(direction),
            ))
        return all_verts

    def _get_fire_mesh_data(self):
        from game.block.fire_rules import FLOOR, WEST, EAST, NORTH, SOUTH, CEILING, ALT_ROW, MIRROR_U

        px, py, pz = self.pos
        flags = int(getattr(self, "_mesh_meta", None) or FLOOR)
        tile = 0.0625
        inset = tile * (15.99 / 16.0)
        color = default_lighting.face_color((0, 1, 0), 15)
        out = []

        def tile_uv(index):
            u, v = self._atlas_uv(index)
            return u, u + inset, v + tile, v + (tile - inset)

        u0, u1, vt, vb = tile_uv(31)
        ru0, ru1, rvt, rvb = tile_uv(47)   

        def quad(points):
            verts = [(px + jx - 0.5, py + jy - 0.5, pz + jz - 0.5)
                     for jx, jy, jz, _u, _v in points]
            uvs = tuple((u, v) for _x, _y, _z, u, v in points)
            out.append((verts, uvs, False, False, color))

        if flags & FLOOR:
            t = 1.4
            quad([(0.2, t, 1, u1, vt), (0.7, 0, 1, u1, vb), (0.7, 0, 0, u0, vb), (0.2, t, 0, u0, vt)])
            quad([(0.8, t, 0, u1, vt), (0.3, 0, 0, u1, vb), (0.3, 0, 1, u0, vb), (0.8, t, 1, u0, vt)])
            quad([(1, t, 0.8, ru1, rvt), (1, 0, 0.7, ru1, rvb), (0, 0, 0.7, ru0, rvb), (0, t, 0.8, ru0, rvt)])
            quad([(0, t, 0.2, ru1, rvt), (0, 0, 0.3, ru1, rvb), (1, 0, 0.3, ru0, rvb), (1, t, 0.2, ru0, rvt)])
            quad([(0.1, t, 0, ru0, rvt), (0, 0, 0, ru0, rvb), (0, 0, 1, ru1, rvb), (0.1, t, 1, ru1, rvt)])
            quad([(0.9, t, 1, ru0, rvt), (1, 0, 1, ru0, rvb), (1, 0, 0, ru1, rvb), (0.9, t, 0, ru1, rvt)])
            quad([(0, t, 0.9, u0, vt), (0, 0, 1, u0, vb), (1, 0, 1, u1, vb), (1, t, 0.9, u1, vt)])
            quad([(1, t, 0.1, u0, vt), (1, 0, 0, u0, vb), (0, 0, 0, u1, vb), (0, t, 0.1, u1, vt)])
            return out

        off = 1.0 / 16.0
        top = 1.4 + off
        bot = off
        wvt, wvb = (rvt, rvb) if flags & ALT_ROW else (vt, vb)
        wa, wb = (u1, u0) if flags & MIRROR_U else (u0, u1)
        if flags & WEST:
            quad([(0.2, top, 1, wb, wvt), (0, bot, 1, wb, wvb), (0, bot, 0, wa, wvb), (0.2, top, 0, wa, wvt)])
            quad([(0.2, top, 0, wa, wvt), (0, bot, 0, wa, wvb), (0, bot, 1, wb, wvb), (0.2, top, 1, wb, wvt)])
        if flags & EAST:
            quad([(0.8, top, 0, wa, wvt), (1, bot, 0, wa, wvb), (1, bot, 1, wb, wvb), (0.8, top, 1, wb, wvt)])
            quad([(0.8, top, 1, wb, wvt), (1, bot, 1, wb, wvb), (1, bot, 0, wa, wvb), (0.8, top, 0, wa, wvt)])
        if flags & NORTH:
            quad([(0, top, 0.2, wb, wvt), (0, bot, 0, wb, wvb), (1, bot, 0, wa, wvb), (1, top, 0.2, wa, wvt)])
            quad([(1, top, 0.2, wa, wvt), (1, bot, 0, wa, wvb), (0, bot, 0, wb, wvb), (0, top, 0.2, wb, wvt)])
        if flags & SOUTH:
            quad([(1, top, 0.8, wa, wvt), (1, bot, 1, wa, wvb), (0, bot, 1, wb, wvb), (0, top, 0.8, wb, wvt)])
            quad([(0, top, 0.8, wb, wvt), (0, bot, 1, wb, wvb), (1, bot, 1, wa, wvb), (1, top, 0.8, wa, wvt)])

        if flags & CEILING:
            tip = 0.8
            if flags & ALT_ROW:
                quad([(0, tip, 0, u1, vt), (1, 1, 0, u1, vb), (1, 1, 1, u0, vb), (0, tip, 1, u0, vt)])
                quad([(1, tip, 1, ru1, rvt), (0, 1, 1, ru1, rvb), (0, 1, 0, ru0, rvb), (1, tip, 0, ru0, rvt)])
            else:
                quad([(0, tip, 1, u1, vt), (0, 1, 0, u1, vb), (1, 1, 0, u0, vb), (1, tip, 1, u0, vt)])
                quad([(1, tip, 0, ru1, rvt), (1, 1, 1, ru1, rvb), (0, 1, 1, ru0, rvb), (0, tip, 0, ru0, rvt)])
        return out

    def _get_crops_mesh_data(self):
        px, py, pz = self.pos
        y0 = -0.5 - (1.0 / 16.0)
        y1 = 0.5 - (1.0 / 16.0)
        all_verts = []
        tile = 0.0625
        faces_def = self.faces
        atlas_idx = faces_def if not isinstance(faces_def, list) else faces_def[0]
        meta = getattr(self, "_mesh_meta", None)
        if meta is None and self.world is not None:
            try:
                meta = self.world.get_metadata(int(self.pos[0]), int(self.pos[1]), int(self.pos[2]))
            except Exception:
                meta = 0
        u, v = self._atlas_uv(atlas_idx + meta)
        uvs = (
            (u, v + tile), (u, v),
            (u + tile, v), (u + tile, v + tile),
        )
        color = default_lighting.face_color((0, 1, 0))

        planes = [
            ((-0.25, y1, -0.5), (-0.25, y0, -0.5), (-0.25, y0, 0.5), (-0.25, y1, 0.5)),
            ((0.25, y1, 0.5), (0.25, y0, 0.5), (0.25, y0, -0.5), (0.25, y1, -0.5)),
            ((-0.5, y1, -0.25), (-0.5, y0, -0.25), (0.5, y0, -0.25), (0.5, y1, -0.25)),
            ((0.5, y1, 0.25), (0.5, y0, 0.25), (-0.5, y0, 0.25), (-0.5, y1, 0.25)),
        ]
        for quad in planes:
            verts = [(px + x, py + y, pz + z) for x, y, z in quad]
            all_verts.append((verts, uvs, False, False, color))
            all_verts.append((
                list(reversed(verts)),
                (uvs[3], uvs[2], uvs[1], uvs[0]),
                False, False, color,
            ))
        return all_verts


    def _get_torch_mesh_data(self):
        px, py, pz = self.pos
        meta = int(getattr(self, "_mesh_meta", None) or 0) & 7
        if meta == 0:
            meta = 5

        all_verts = []
        tile = 0.0625
        faces_def = self.faces
        atlas_idx = faces_def if not isinstance(faces_def, list) else faces_def[0]
        u, v = self._atlas_uv(atlas_idx)
        u0, u1 = u, u + tile
        v0, v1 = v, v + tile
        stick_uv = ((u0, v1), (u0, v0), (u1, v0), (u1, v1))
        cu0 = u + (7.0 / 16.0) * tile
        cu1 = u + (9.0 / 16.0) * tile
        cv0 = v + (8.0 / 16.0) * tile
        cv1 = v + (10.0 / 16.0) * tile
        cap_uv = ((cu0, cv0), (cu0, cv1), (cu1, cv1), (cu1, cv0))
        color = default_lighting.face_color((0, 1, 0), 15)

        tilt = 0.4
        raise_y = 0.2
        if meta == 1:
            ang_x, ang_z = -tilt, 0.0
            top_x, top_z, y0 = -0.1, 0.0, -0.5 + raise_y
        elif meta == 2:
            ang_x, ang_z = tilt, 0.0
            top_x, top_z, y0 = 0.1, 0.0, -0.5 + raise_y
        elif meta == 3:
            ang_x, ang_z = 0.0, -tilt
            top_x, top_z, y0 = 0.0, -0.1, -0.5 + raise_y
        elif meta == 4:
            ang_x, ang_z = 0.0, tilt
            top_x, top_z, y0 = 0.0, 0.1, -0.5 + raise_y
        else:
            ang_x, ang_z = 0.0, 0.0
            top_x, top_z, y0 = 0.0, 0.0, -0.5

        y1 = y0 + 1.0
        cap_y = y0 + 0.625
        t = 1.0 / 16.0

        def emit(quad, uv):
            verts = [(px + x, py + y, pz + z) for x, y, z in quad]
            all_verts.append((verts, uv, False, False, color))
            all_verts.append((
                list(reversed(verts)),
                (uv[3], uv[2], uv[1], uv[0]),
                False, False, color,
            ))

        for sx in (-t, t):
            if sx < 0:
                quad = (
                    (top_x + sx, y1, top_z - 0.5),
                    (top_x + sx + ang_x, y0, top_z - 0.5 + ang_z),
                    (top_x + sx + ang_x, y0, top_z + 0.5 + ang_z),
                    (top_x + sx, y1, top_z + 0.5),
                )
            else:
                quad = (
                    (top_x + sx, y1, top_z + 0.5),
                    (top_x + sx + ang_x, y0, top_z + 0.5 + ang_z),
                    (top_x + sx + ang_x, y0, top_z - 0.5 + ang_z),
                    (top_x + sx, y1, top_z - 0.5),
                )
            emit(quad, stick_uv)

        for sz in (-t, t):
            if sz < 0:
                quad = (
                    (top_x - 0.5, y1, top_z + sz),
                    (top_x - 0.5 + ang_x, y0, top_z + sz + ang_z),
                    (top_x + 0.5 + ang_x, y0, top_z + sz + ang_z),
                    (top_x + 0.5, y1, top_z + sz),
                )
            else:
                quad = (
                    (top_x + 0.5, y1, top_z + sz),
                    (top_x + 0.5 + ang_x, y0, top_z + sz + ang_z),
                    (top_x - 0.5 + ang_x, y0, top_z + sz + ang_z),
                    (top_x - 0.5, y1, top_z + sz),
                )
            emit(quad, stick_uv)

        cx = top_x + ang_x * (1.0 - 0.625)
        cz = top_z + ang_z * (1.0 - 0.625)
        cap = (
            (cx - t, cap_y, cz - t),
            (cx - t, cap_y, cz + t),
            (cx + t, cap_y, cz + t),
            (cx + t, cap_y, cz - t),
        )
        emit(cap, cap_uv)
        return all_verts


    def _get_ladder_mesh_data(self):
        px, py, pz = self.pos
        meta = getattr(self, "_mesh_meta", 2) or 2
        tile = 0.0625
        faces_def = self.faces
        atlas_idx = faces_def if not isinstance(faces_def, list) else faces_def[0]
        u, v = self._atlas_uv(atlas_idx)
        uvs = ((u, v), (u + tile, v), (u + tile, v + tile), (u, v + tile))
        color = default_lighting.face_color((0, 1, 0))
        t = 0.5 - (1.0 / 16.0)

        if meta == 2:
            quad = ((-0.5, -0.5, t), (0.5, -0.5, t), (0.5, 0.5, t), (-0.5, 0.5, t))
        elif meta == 3:
            quad = ((0.5, -0.5, -t), (-0.5, -0.5, -t), (-0.5, 0.5, -t), (0.5, 0.5, -t))
        elif meta == 4:
            quad = ((t, -0.5, 0.5), (t, -0.5, -0.5), (t, 0.5, -0.5), (t, 0.5, 0.5))
        else:  
            quad = ((-t, -0.5, -0.5), (-t, -0.5, 0.5), (-t, 0.5, 0.5), (-t, 0.5, -0.5))

        verts = [(px + x, py + y, pz + z) for x, y, z in quad]
        return [
            (verts, uvs, False, False, color),
            (list(reversed(verts)), (uvs[3], uvs[2], uvs[1], uvs[0]), False, False, color),
        ]


    def _get_slab_mesh_data(self):
        px, py, pz = self.pos
        faces_def = self.faces
        is_list = isinstance(faces_def, list)
        face_map = self._FACE_MAP
        top_y, bot_y = 0.0, -0.5

        def _uv(name, quad, direction):
            if is_list:
                idx = faces_def[face_map.get(name, 0)]
            else:
                idx = faces_def
            return self._face_uvs_for_quad(quad, idx, direction)

        faces = [
            ("face_top", (0, 1, 0),
             ((-0.5, top_y, 0.5), (0.5, top_y, 0.5), (0.5, top_y, -0.5), (-0.5, top_y, -0.5))),
            ("face_bottom", (0, -1, 0),
             ((-0.5, bot_y, -0.5), (0.5, bot_y, -0.5), (0.5, bot_y, 0.5), (-0.5, bot_y, 0.5))),
            ("face_front", (0, 0, 1),
             ((-0.5, bot_y, 0.5), (0.5, bot_y, 0.5), (0.5, top_y, 0.5), (-0.5, top_y, 0.5))),
            ("face_back", (0, 0, -1),
             ((0.5, bot_y, -0.5), (-0.5, bot_y, -0.5), (-0.5, top_y, -0.5), (0.5, top_y, -0.5))),
            ("face_right", (1, 0, 0),
             ((0.5, bot_y, 0.5), (0.5, bot_y, -0.5), (0.5, top_y, -0.5), (0.5, top_y, 0.5))),
            ("face_left", (-1, 0, 0),
             ((-0.5, bot_y, -0.5), (-0.5, bot_y, 0.5), (-0.5, top_y, 0.5), (-0.5, top_y, -0.5))),
        ]
        out = []
        for name, direction, quad in faces:
            if self._should_cull(direction, name):
                continue
            verts = [(px + x, py + y, pz + z) for x, y, z in quad]
            out.append((
                direction, verts, _uv(name, quad, direction), True, False,
                default_lighting.face_color(direction),
            ))
        return out


    def _get_stairs_mesh_data(self):
        px, py, pz = self.pos
        meta = self._resolve_meta()
        facing, upside_down = meta & 3, bool(meta & 4)
        faces_def = self.faces

        def atlas_for(slot):
            if isinstance(faces_def, list):
                return faces_def[slot] if slot < len(faces_def) else faces_def[0]
            return faces_def

        def rotate_point(point):
            x, y, z = point
            if upside_down:
                y = -y
            if facing == 0:
                x, z = -z, x
            elif facing == 1:
                x, z = z, -x
            elif facing == 2:
                x, z = -x, -z
            return (px + x, py + y, pz + z)

        def rotate_dir(direction):
            x, y, z = direction
            if upside_down:
                y = -y
            if facing == 0:
                x, z = -z, x
            elif facing == 1:
                x, z = z, -x
            elif facing == 2:
                x, z = -x, -z
            return (x, y, z)

        out = []
        def emit(direction, quad, slot):
            idx = atlas_for(slot)
            if upside_down and slot in (2, 3):
                idx = atlas_for(5 - slot)
            verts = [rotate_point(p) for p in quad]
            normal = rotate_dir(direction)
            uvs = self._face_uvs_for_quad(quad, idx, direction)
            out.append((normal, verts, uvs, True, self.transparent,
                        default_lighting.face_color(normal)))

        yb, ym, yt = -0.5, 0.0, 0.5
        emit((0, -1, 0), ((-0.5, yb, -0.5), (0.5, yb, -0.5),
                           (0.5, yb, 0.5), (-0.5, yb, 0.5)), 2)
        emit((0, 1, 0), ((-0.5, yt, -0.5), (-0.5, yt, 0.0),
                         (0.5, yt, 0.0), (0.5, yt, -0.5)), 3)
        emit((0, 1, 0), ((-0.5, ym, 0.0), (-0.5, ym, 0.5),
                         (0.5, ym, 0.5), (0.5, ym, 0.0)), 3)
        emit((0, 0, 1), ((-0.5, yb, 0.5), (0.5, yb, 0.5),
                         (0.5, ym, 0.5), (-0.5, ym, 0.5)), 4)
        emit((0, 0, -1), ((0.5, yb, -0.5), (-0.5, yb, -0.5),
                          (-0.5, yt, -0.5), (0.5, yt, -0.5)), 5)
        emit((0, 0, 1), ((-0.5, ym, 0.0), (0.5, ym, 0.0),
                         (0.5, yt, 0.0), (-0.5, yt, 0.0)), 4)
        emit((-1, 0, 0), ((-0.5, yb, 0.5), (-0.5, ym, 0.5),
                          (-0.5, ym, -0.5), (-0.5, yb, -0.5)), 1)
        emit((-1, 0, 0), ((-0.5, ym, 0.0), (-0.5, yt, 0.0),
                          (-0.5, yt, -0.5), (-0.5, ym, -0.5)), 1)
        emit((1, 0, 0), ((0.5, yb, -0.5), (0.5, ym, -0.5),
                         (0.5, ym, 0.5), (0.5, yb, 0.5)), 0)
        emit((1, 0, 0), ((0.5, ym, -0.5), (0.5, yt, -0.5),
                         (0.5, yt, 0.0), (0.5, ym, 0.0)), 0)
        return out

    def _get_door_mesh_data(self):
        px, py, pz = self.pos
        raw_meta = self._resolve_meta()
        is_upper = bool(raw_meta & 8)
        lower_meta = raw_meta & 7
        block_x, block_y, block_z = (int(px), int(py), int(pz))
        if is_upper and self.world is not None:
            try:
                if self.world.get_block_type(block_x, block_y - 1, block_z) == self.block_type:
                    lower_meta = int(self.world.get_metadata(block_x, block_y - 1, block_z)) & 7
            except Exception:
                pass

        closed_state = (lower_meta - 1) & 3

        mirror_right = self._door_is_right_leaf(
            closed_state, block_y - 1 if is_upper else block_y
        )
        is_open = bool(lower_meta & 4)
        state = (lower_meta & 3) if is_open else closed_state
        if is_open and mirror_right:
            state = (closed_state - 1) & 3
        t = 3.0 / 16.0
        if state == 0:
            x0, x1, z0, z1 = -0.5, 0.5, -0.5, -0.5 + t
        elif state == 1:
            x0, x1, z0, z1 = 0.5 - t, 0.5, -0.5, 0.5
        elif state == 2:
            x0, x1, z0, z1 = -0.5, 0.5, 0.5 - t, 0.5
        else:
            x0, x1, z0, z1 = -0.5, -0.5 + t, -0.5, 0.5

        y0, y1 = -0.5, 0.5
        side_for_normal = {
            (0, 0, -1): 2, (0, 0, 1): 3,
            (-1, 0, 0): 4, (1, 0, 0): 5,
        }
        base_index = 97 if self.block_type == "wooden_door" else 98
        metadata_for_texture = (lower_meta | 8) if is_upper else lower_meta

        quads = [
            ((0, 0, 1),  ((x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1))),
            ((0, 0, -1), ((x1, y0, z0), (x0, y0, z0), (x0, y1, z0), (x1, y1, z0))),
            ((-1, 0, 0), ((x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0))),
            ((1, 0, 0),  ((x1, y0, z1), (x1, y0, z0), (x1, y1, z0), (x1, y1, z1))),
            ((0, 1, 0),  ((x0, y1, z1), (x1, y1, z1), (x1, y1, z0), (x0, y1, z0))),
            ((0, -1, 0), ((x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1))),
        ]
        out = []
        for direction, quad in quads:
            idx = base_index
            mirror_u = False
            if direction[1] == 0:
                side = side_for_normal[direction]
                if (state in (0, 2)) ^ (side <= 3):
                    idx = base_index
                else:
                    var4 = state // 2 + ((side & 1) ^ state)
                    var4 += (metadata_for_texture & 4) // 4
                    idx = base_index - (metadata_for_texture & 8) * 2
                    mirror_u = bool(var4 & 1)

                is_broad_panel = (
                    (state in (0, 2) and direction[0] == 0 and direction[2] != 0)
                    or (state in (1, 3) and direction[2] == 0 and direction[0] != 0)
                )
                if mirror_right and is_broad_panel:
                    mirror_u = not mirror_u
            verts = [(px + x, py + y, pz + z) for x, y, z in quad]
            uvs = self._face_uvs_for_quad(quad, idx, direction)
            if mirror_u:
                u0, _v0 = self._atlas_uv(idx)
                tile = 1.0 / 16.0
                uvs = tuple((2 * u0 + tile - u, v) for u, v in uvs)
            out.append((direction, verts, uvs, True, False,
                        default_lighting.face_color(direction)))
        return out

    def _get_rail_mesh_data(self):
        px, py, pz = self.pos
        meta = int(getattr(self, "_mesh_meta", 0) or 0) & 15
        tile = 0.0625
        inset = tile * (15.99 / 16.0)
        faces_def = self.faces
        atlas_idx = faces_def if not isinstance(faces_def, list) else faces_def[0]
        if meta >= 6:
            atlas_idx = max(0, int(atlas_idx) - 16)
        u, v = self._atlas_uv(atlas_idx)
        u0, u1 = u, u + inset
        vt, vb = v + tile, v + (tile - inset)

        if meta in (1, 2, 3, 7):
            corners = ((1, 1), (0, 1), (0, 0), (1, 0))
        elif meta == 8:
            corners = ((0, 1), (0, 0), (1, 0), (1, 1))
        elif meta == 9:
            corners = ((0, 0), (1, 0), (1, 1), (0, 1))
        else:
            corners = ((1, 0), (1, 1), (0, 1), (0, 0))
        heights = [1.0 / 16.0] * 4
        if meta in (2, 4):
            heights[0] += 1.0
            heights[3] += 1.0
        elif meta in (3, 5):
            heights[1] += 1.0
            heights[2] += 1.0

        uvs = ((u1, vt), (u1, vb), (u0, vb), (u0, vt))
        verts = [
            (px + cx - 0.5, py + h - 0.5, pz + cz - 0.5)
            for (cx, cz), h in zip(corners, heights)
        ]
        color = (1.0, 1.0, 1.0)
        return [
            (verts, uvs, False, False, color),
            (list(reversed(verts)), (uvs[3], uvs[2], uvs[1], uvs[0]), False, False, color),
        ]


    def _get_cake_mesh_data(self):
        meta = min(5, self._resolve_meta())
        x0 = (1.0 + meta * 2.0) / 16.0
        x1 = 15.0 / 16.0
        y0, y1 = 0.0, 8.0 / 16.0
        z0, z1 = 1.0 / 16.0, 15.0 / 16.0
        px, py, pz = (float(v) for v in self.pos)
        out = []
        atlas_epsilon = 0.01 / 256.0

        for _name, direction, original_quad, cull_backface in self._CUBE_FACES:
            local_quad = []
            for vx, vy, vz in original_quad:
                bx, by, bz = vx + 0.5, vy + 0.5, vz + 0.5
                local_quad.append((
                    x0 + bx * (x1 - x0) - 0.5,
                    y0 + by * (y1 - y0) - 0.5,
                    z0 + bz * (z1 - z0) - 0.5,
                ))

            if direction == (0, 1, 0):
                texture = 121  
            elif direction == (0, -1, 0):
                texture = 124  
            elif direction == (-1, 0, 0) and meta > 0:
                texture = 123  
            else:
                texture = 122  

            u0, v0 = self._atlas_uv(texture)
            uvs = []
            for vx, vy, vz in local_quad:
                bx, by, bz = vx + 0.5, vy + 0.5, vz + 0.5
                if direction == (0, 1, 0) or direction == (0, -1, 0):
                    uf, vf = bx, bz
                elif direction == (0, 0, 1):
                    uf, vf = bx, 0.5 + by
                elif direction == (0, 0, -1):
                    uf, vf = (x0 + x1) - bx, 0.5 + by
                elif direction == (-1, 0, 0):
                    uf, vf = bz, 0.5 + by
                else:
                    uf, vf = (z0 + z1) - bz, 0.5 + by

                u = u0 + uf / 16.0
                v = v0 + vf / 16.0
                if abs(uf - x1) < 1e-8 or abs(uf - z1) < 1e-8 or abs(uf - y1) < 1e-8:
                    u -= atlas_epsilon
                if abs(vf - z1) < 1e-8 or abs(vf - 1.0) < 1e-8:
                    v -= atlas_epsilon
                uvs.append((u, v))

            vertices = [(px + vx, py + vy, pz + vz) for vx, vy, vz in local_quad]
            color = default_lighting.face_color(direction)
            out.append((vertices, tuple(uvs), cull_backface, False, color))
        return out

    def _get_cube_mesh_data(self):
        px, py, pz = self.pos
        all_verts = []
        faces_def = self.faces
        is_list = isinstance(faces_def, list)
        transparent = self.transparent
        face_map = self._FACE_MAP
        tile = 0.0625

        if (is_list and self.block_type == "grass_block"
                and getattr(self, "_mesh_meta", 0) == 1):
            faces_def = list(faces_def)
            for side_index in (0, 1, 4, 5):
                faces_def[side_index] = 68

        if is_list and self.block_type in (
            "furnace", "furnace_lit", "chest", "dispenser", "pumpkin", "jack_o_lantern",
        ) and not (
            self.block_type == "chest" and getattr(self, "_chest_faces_override", False)
        ):
            meta = getattr(self, "_mesh_meta", None)
            if self.world is not None and self.block_type == "dispenser":
                try:
                    meta = self.world.get_metadata(
                        int(self.pos[0]), int(self.pos[1]), int(self.pos[2]),
                    )
                except Exception:
                    pass
            elif meta is None and self.world is not None:
                try:
                    meta = self.world.get_metadata(int(self.pos[0]), int(self.pos[1]), int(self.pos[2]))
                except Exception:
                    meta = 0
            meta = int(meta or 0)
            if self.block_type in ("pumpkin", "jack_o_lantern"):
                meta = {0: 2, 1: 5, 2: 3, 3: 4}[meta & 3]
            r, l, bot, top, f, b = (faces_def + [faces_def[0]] * 6)[:6]
            if meta == 2:
                faces_def = [r, l, bot, top, b, f]
            elif meta == 3:
                faces_def = [r, l, bot, top, f, b]
            elif meta == 4:
                faces_def = [b, f, bot, top, r, l]
            elif meta == 5:
                faces_def = [f, b, bot, top, l, r]

        for name, direction, local_quad, cull_backface in self._CUBE_FACES:
            if self._should_cull(direction, name):
                continue

            if is_list:
                atlas_idx = faces_def[face_map.get(name, 0)]
            else:
                atlas_idx = faces_def

            u, v = self._atlas_uv(atlas_idx)
            uvs = (
                (u, v), (u + tile, v),
                (u + tile, v + tile), (u, v + tile),
            )

            rotated = [
                (px + lx, py + ly, pz + lz) for lx, ly, lz in local_quad
            ]

            color = default_lighting.face_color(direction)
            all_verts.append((rotated, uvs, cull_backface, transparent, color))

        return all_verts

    def _get_generic_mesh_data(self):
        all_verts = []
        px, py, pz = self.pos
        faces_def = self.faces
        is_list = isinstance(faces_def, list)
        face_map = self._FACE_MAP
        tile = 0.0625

        for name, data in self.model.items():
            x, y, z, pitch, yaw, roll = data[:6]
            cull_backface = data[6] if len(data) > 6 else True

            direction = self._face_direction((pitch, yaw, roll))
            if self._should_cull(direction, name):
                continue

            if is_list:
                atlas_idx = faces_def[face_map.get(name, 0)]
            else:
                atlas_idx = faces_def

            u, v = self._atlas_uv(atlas_idx)
            uvs = (
                (u, v), (u + tile, v),
                (u + tile, v + tile), (u, v + tile),
            )

            p = math.radians(pitch)
            ya = math.radians(yaw)
            r = math.radians(roll)
            cp, sp = math.cos(p), math.sin(p)
            cy, sy = math.cos(ya), math.sin(ya)
            cr, sr = math.cos(r), math.sin(r)

            ryx00, ryx01, ryx02 = cy, sp * sy, cp * sy
            ryx10, ryx11, ryx12 = 0.0, cp, -sp
            ryx20, ryx21, ryx22 = -sy, sp * cy, cp * cy

            m00 = cr * ryx00 - sr * ryx10
            m01 = cr * ryx01 - sr * ryx11
            m02 = cr * ryx02 - sr * ryx12
            m10 = sr * ryx00 + cr * ryx10
            m11 = sr * ryx01 + cr * ryx11
            m12 = sr * ryx02 + cr * ryx12
            m20 = ryx20
            m21 = ryx21
            m22 = ryx22

            quad = (
                (-0.5, -0.5, 0.0), (0.5, -0.5, 0.0),
                (0.5, 0.5, 0.0), (-0.5, 0.5, 0.0),
            )

            rotated = []
            for vx, vy, vz in quad:
                rx = m00 * vx + m01 * vy + m02 * vz + x + px
                ry = m10 * vx + m11 * vy + m12 * vz + y + py
                rz = m20 * vx + m21 * vy + m22 * vz + z + pz
                rotated.append((rx, ry, rz))

            color = default_lighting.face_color(direction)
            all_verts.append((rotated, uvs, cull_backface, self.transparent, color))

        return all_verts

    def _face_direction(self, rot):
        pitch, yaw, roll = rot

        if pitch == 90:
            return 0, -1, 0
        if pitch == -90:
            return 0, 1, 0
        if yaw == 90:
            return 1, 0, 0
        if yaw == -90:
            return -1, 0, 0
        if yaw == 180:
            return 0, 0, -1

        return 0, 0, 1