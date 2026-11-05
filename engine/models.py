import math

from .helpers import Helpers
from .textures import Texture


class Model:
    renderer = None

class OBJLoader:
    _cache = {}  
    
    @staticmethod
    def load(filepath):
        if filepath in OBJLoader._cache:
            return OBJLoader._cache[filepath]
        
        vertices = []
        faces = []
        
        try:
            with open(filepath, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    
                    parts = line.split()
                    if not parts:
                        continue
                    
                    if parts[0] == 'v':
                        x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
                        vertices.append((x, y, z))
                    
                    elif parts[0] == 'f':
                        indices = []
                        for part in parts[1:]:
                            idx = int(part.split('/')[0])
                            indices.append(idx - 1)
                        
                        if len(indices) == 3:
                            faces.append(tuple(indices))
                        elif len(indices) == 4:
                            faces.append((indices[0], indices[1], indices[2]))
                            faces.append((indices[0], indices[2], indices[3]))
                        elif len(indices) > 4:
                            for i in range(1, len(indices) - 1):
                                faces.append((indices[0], indices[i], indices[i + 1]))
        
        except FileNotFoundError:
            print(f"Warning: OBJ file not found: {filepath}")
            OBJLoader._cache[filepath] = ([], [])
            return [], []
        except Exception as e:
            print(f"Error loading OBJ file {filepath}: {e}")
            OBJLoader._cache[filepath] = ([], [])
            return [], []
        
        OBJLoader._cache[filepath] = (vertices, faces)
        return vertices, faces

class Mesh(Model):
    def __init__(self, id, path, position, rotation, size, texture=None):
        self.id = id
        self.path = path
        self.position = position
        self.rotation = rotation
        self.size = size
        self.color = (255, 255, 255)
        self.texture = None
        self.texture_coords = None
        
        self.vertices, self.faces = OBJLoader.load(path)
        
        if texture is not None:
            if isinstance(texture, str):
                self.texture = Texture(texture)
            else:
                self.texture = texture
            self._generate_mesh_uv_coords()
    
    def _generate_mesh_uv_coords(self):
        self.texture_coords = []
        for face_idx in range(len(self.faces)):
            self.texture_coords.append((self.texture.x, self.texture.y))
            self.texture_coords.append((self.texture.x + self.texture.u_size, self.texture.y))
            self.texture_coords.append((self.texture.x + self.texture.u_size, self.texture.y + self.texture.v_size))
    
    def draw(self):
        if not self.vertices or not self.faces:
            return
        
        self.renderer.draw_mesh(
            self.vertices,
            self.faces,
            self.position,
            self.rotation,
            self.size,
            self.color,
            texture=self.texture,
            tex_coords=self.texture_coords
        )

class Cube(Model):
    def __init__(self, id, position, rotation, size, color, texture=None):
        self.id = id
        self.position = position
        self.rotation = rotation
        self.size = size
        self.color = color
        self.path = "resources/primitives/cube.obj"
        self.texture = None
        self.texture_coords = None
        
        self.vertices, self.faces = OBJLoader.load(self.path)
        
        if texture is not None:
            if isinstance(texture, str):
                self.texture = Texture(texture)
            else:
                self.texture = texture
            self._generate_cube_uv_coords()

    def _generate_cube_uv_coords(self):
        self.texture_coords = []
        
        uv_patterns = [
            [(self.texture.x, self.texture.y), 
             (self.texture.x + self.texture.u_size, self.texture.y), 
             (self.texture.x + self.texture.u_size, self.texture.y + self.texture.v_size)],  
            [(self.texture.x, self.texture.y), 
             (self.texture.x + self.texture.u_size, self.texture.y + self.texture.v_size), 
             (self.texture.x, self.texture.y + self.texture.v_size)],  
        ]
        
        for _ in range(len(self.faces) // 2):
            for pattern in uv_patterns:
                self.texture_coords.extend(pattern)

    def draw(self):
        if not self.vertices or not self.faces:
            return
        
        self.renderer.draw_mesh(
            self.vertices,
            self.faces,
            self.position,
            self.rotation,
            self.size,
            self.color,
            texture=self.texture,
            tex_coords=self.texture_coords
        )

class Sphere(Model):
    def __init__(self, id, position, rotation, size, color, texture=None):
        self.id = id
        self.position = position
        self.rotation = rotation
        self.size = size
        self.color = color
        self.path = "resources/primitives/sphere.obj"
        self.texture = None
        self.texture_coords = None
        
        self.vertices, self.faces = OBJLoader.load(self.path)
        
        if texture is not None:
            if isinstance(texture, str):
                self.texture = Texture(texture)
            else:
                self.texture = texture
            self._generate_sphere_uv_coords()

    def _generate_sphere_uv_coords(self):
        self.texture_coords = []
        for face_idx in range(len(self.faces)):
            self.texture_coords.append((self.texture.x, self.texture.y))
            self.texture_coords.append((self.texture.x + self.texture.u_size, self.texture.y))
            self.texture_coords.append((self.texture.x + self.texture.u_size, self.texture.y + self.texture.v_size))

    def draw(self):
        if not self.vertices or not self.faces:
            return
        
        self.renderer.draw_mesh(
            self.vertices,
            self.faces,
            self.position,
            self.rotation,
            self.size,
            self.color,
            texture=self.texture,
            tex_coords=self.texture_coords
        )

class Cone(Model):
    def __init__(self, id, position, rotation, size, color, texture=None):
        self.id = id
        self.position = position
        self.rotation = rotation
        self.size = size
        self.color = color
        self.path = "resources/primitives/cone.obj"
        self.texture = None
        self.texture_coords = None
        
        self.vertices, self.faces = OBJLoader.load(self.path)
        
        if texture is not None:
            if isinstance(texture, str):
                self.texture = Texture(texture)
            else:
                self.texture = texture
            self._generate_cone_uv_coords()

    def _generate_cone_uv_coords(self):
        self.texture_coords = []
        for face_idx in range(len(self.faces)):
            self.texture_coords.append((self.texture.x, self.texture.y))
            self.texture_coords.append((self.texture.x + self.texture.u_size, self.texture.y))
            self.texture_coords.append((self.texture.x + self.texture.u_size, self.texture.y + self.texture.v_size))

    def draw(self):
        if not self.vertices or not self.faces:
            return
        
        self.renderer.draw_mesh(
            self.vertices,
            self.faces,
            self.position,
            self.rotation,
            self.size,
            self.color,
            texture=self.texture,
            tex_coords=self.texture_coords
        )

class Cylinder(Model):
    def __init__(self, id, position, rotation, size, color, texture=None):
        self.id = id
        self.position = position
        self.rotation = rotation
        self.size = size
        self.color = color
        self.path = "resources/primitives/cylinder.obj"
        self.texture = None
        self.texture_coords = None
        
        self.vertices, self.faces = OBJLoader.load(self.path)
        
        if texture is not None:
            if isinstance(texture, str):
                self.texture = Texture(texture)
            else:
                self.texture = texture
            self._generate_cylinder_uv_coords()

    def _generate_cylinder_uv_coords(self):
        self.texture_coords = []
        for face_idx in range(len(self.faces)):
            self.texture_coords.append((self.texture.x, self.texture.y))
            self.texture_coords.append((self.texture.x + self.texture.u_size, self.texture.y))
            self.texture_coords.append((self.texture.x + self.texture.u_size, self.texture.y + self.texture.v_size))

    def draw(self):
        if not self.vertices or not self.faces:
            return
        
        self.renderer.draw_mesh(
            self.vertices,
            self.faces,
            self.position,
            self.rotation,
            self.size,
            self.color,
            texture=self.texture,
            tex_coords=self.texture_coords
        )

class Torus(Model):
    def __init__(self, id, position, rotation, size, color, texture=None):
        self.id = id
        self.position = position
        self.rotation = rotation
        self.size = size
        self.color = color
        self.path = "resources/primitives/torus.obj"
        self.texture = None
        self.texture_coords = None
        
        self.vertices, self.faces = OBJLoader.load(self.path)
        
        if texture is not None:
            if isinstance(texture, str):
                self.texture = Texture(texture)
            else:
                self.texture = texture
            self._generate_torus_uv_coords()

    def _generate_torus_uv_coords(self):
        self.texture_coords = []
        for face_idx in range(len(self.faces)):
            self.texture_coords.append((self.texture.x, self.texture.y))
            self.texture_coords.append((self.texture.x + self.texture.u_size, self.texture.y))
            self.texture_coords.append((self.texture.x + self.texture.u_size, self.texture.y + self.texture.v_size))

    def draw(self):
        if not self.vertices or not self.faces:
            return
        
        self.renderer.draw_mesh(
            self.vertices,
            self.faces,
            self.position,
            self.rotation,
            self.size,
            self.color,
            texture=self.texture,
            tex_coords=self.texture_coords
        )

class Error(Model):
    def __init__(self, position):
        self.id = "error_" + Helpers.gen_uuid()
        self.position = position
        self.path = "resources/error.obj"
        
        self.vertices, self.faces = OBJLoader.load(self.path)

    def draw(self):
        if not self.vertices or not self.faces:
            return
        
        self.renderer.draw_mesh(
            self.vertices,
            self.faces,
            self.position,
            (0, 0, 0),
            (1, 1, 1),
            (255, 0, 0)
        )

class Wishbone(Model):
    def __init__(self, id, pos, useArcs):
        self.id = id
        self.pos = pos
        self.useArcs = useArcs
        
    def draw(self):
        self.renderer.line_3d(self.pos, (self.pos[0], self.pos[1] + 1, self.pos[2]), (0, 255, 0))
        self.renderer.line_3d(self.pos, (self.pos[0] + 1, self.pos[1], self.pos[2]), (255, 0, 0))
        self.renderer.line_3d(self.pos, (self.pos[0], self.pos[1], self.pos[2] + 1), (0, 0, 255))

        if self.useArcs:
            radius = 0.8
            segments = 48
            full = 2 * math.pi
            arc_angle = full / 3.0
            center = self.pos

            def draw_arc(angle_start, angle_end, point_fn, color):
                steps = max(3, int(segments * (angle_end - angle_start) / full))
                prev = None
                for s in range(steps + 1):
                    t = angle_start + (angle_end - angle_start) * (s / steps)
                    p = point_fn(t)
                    if prev is not None:
                        self.renderer.line_3d(prev, p, color)
                    prev = p

            for k in range(3):
                a0 = k * arc_angle
                a1 = a0 + arc_angle
                def yz_point(theta):
                    y = math.cos(theta) * radius
                    z = math.sin(theta) * radius
                    return center[0], center[1] + y, center[2] + z
                draw_arc(a0, a1, yz_point, (255, 0, 0))

            for k in range(3):
                a0 = k * arc_angle
                a1 = a0 + arc_angle
                def xz_point(theta):
                    x = math.cos(theta) * radius
                    z = math.sin(theta) * radius
                    return center[0] + x, center[1], center[2] + z
                draw_arc(a0, a1, xz_point, (0, 255, 0))

            for k in range(3):
                a0 = k * arc_angle
                a1 = a0 + arc_angle
                def xy_point(theta):
                    x = math.cos(theta) * radius
                    y = math.sin(theta) * radius
                    return center[0] + x, center[1] + y, center[2]
                draw_arc(a0, a1, xy_point, (0, 0, 255))