
from OpenGL.GL import *
from OpenGL.GLU import *
import math
import numpy as np, pygame as pg


class Renderer:
    def __init__(self):
        self.camPos = [0, 0, 0]  
        self.camRot = [0, 0, 0]  
        self.rqlen = 0  
        self.texture_cache = {}
        self.mesh_cache = {}  
        self.camX = self.camPos[0]
        self.camY = self.camPos[1]
        self.camZ = self.camPos[2]
        self.view_x = self.camX
        self.view_y = self.camY
        self.view_z = self.camZ
        self.pitch = self.camRot[0]
        self.yaw = self.camRot[1]
        self.roll = self.camRot[2]
        self.fov = 70
        self.near_clip = 0.05
        self.far_clip = 128.0
        self.render_distance_index = 1
        self._alpha_fog_brightness = 0.0
        self._alpha_fog_brightness_prev = 0.0
        self._nv_fog_distance_checked = False
        self._supports_nv_fog_distance = False
        self._last_aspect = 1067 / 600

    def sync_view_to_camera(self):
        self.view_x = float(self.camX)
        self.view_y = float(self.camY)
        self.view_z = float(self.camZ)

    def _norm_color(self, color):
        if color is None:
            return 1.0, 1.0, 1.0
        try:
            r, g, b = color
        except Exception:
            return 1.0, 1.0, 1.0
        if max(r, g, b) > 1.0:
            return r / 255.0, g / 255.0, b / 255.0
        return r, g, b

    def setup_3d_projection(self, aspect=None):
        if aspect is None or aspect <= 0:
            aspect = 1067 / 600
        self._last_aspect = float(aspect)

        glEnable(GL_DEPTH_TEST)
        glDepthMask(True)
        glEnable(GL_CULL_FACE)
        glCullFace(GL_BACK)
        glDisable(GL_BLEND)
        glEnable(GL_ALPHA_TEST)
        glAlphaFunc(GL_GREATER, 0.5)
        glColor4f(1.0, 1.0, 1.0, 1.0)
        glEnable(GL_TEXTURE_2D)

        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        gluPerspective(self.fov, aspect, self.near_clip, self.far_clip)

        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()

        glRotatef(-self.pitch, 1, 0, 0)  
        glRotatef(-self.yaw, 0, 1, 0)  
        glRotatef(-self.roll, 0, 0, 1)  

        fog = getattr(self, "fog_color", (0.6, 0.8, 1.0))
        glFogi(GL_FOG_MODE, GL_LINEAR)
        glFogfv(GL_FOG_COLOR, (float(fog[0]), float(fog[1]), float(fog[2]), 1.0))
        glFogf(GL_FOG_START, self.far_clip * 0.25)
        glFogf(GL_FOG_END, self.far_clip)
        self.enable_alpha_radial_fog()
        glHint(GL_FOG_HINT, GL_NICEST)
        glEnable(GL_FOG)

    def update_alpha_fog_brightness(self, world, player):
        from game.rendering.view_culling import alpha_fog_brightness_target

        self._alpha_fog_brightness_prev = self._alpha_fog_brightness
        if world is None or player is None:
            target = 1.0
        else:
            x = math.floor(float(player.pos[0]) + 0.5)
            eye_height = float(getattr(player, "EYE_HEIGHT", 1.62))
            y = math.floor(float(player.pos[1]) + eye_height + 0.5)
            z = math.floor(float(player.pos[2]) + 0.5)
            light_level = world.get_light(x, y, z)
            target = alpha_fog_brightness_target(
                light_level, self.render_distance_index,
            )
        self._alpha_fog_brightness += (
            target - self._alpha_fog_brightness
        ) * 0.1

    def interpolated_alpha_fog_brightness(self, partial_ticks=1.0):
        partial = max(0.0, min(1.0, float(partial_ticks)))
        return self._alpha_fog_brightness_prev + (
            self._alpha_fog_brightness - self._alpha_fog_brightness_prev
        ) * partial

    def enable_alpha_radial_fog(self):
        if not self._nv_fog_distance_checked:
            self._nv_fog_distance_checked = True
            try:
                extensions = glGetString(GL_EXTENSIONS) or b""
                if isinstance(extensions, str):
                    extensions = extensions.encode("ascii", "ignore")
                self._supports_nv_fog_distance = (
                    b"GL_NV_fog_distance" in extensions.split()
                )
            except Exception:
                self._supports_nv_fog_distance = False
        if self._supports_nv_fog_distance:
            glFogi(0x855A, 0x855B)  

    def set_fog_color(self, rgb):
        self.fog_color = (float(rgb[0]), float(rgb[1]), float(rgb[2]))

    def setup_underwater_fog(self, lava=False):
        from OpenGL.GL import (
            GL_EXP, GL_FOG_DENSITY, GL_FOG_MODE, GL_FOG, GL_FOG_COLOR,
            glFogi, glFogf, glFogfv, glEnable,
        )
        fog = getattr(self, "fog_color", (0.02, 0.02, 0.2))
        glEnable(GL_FOG)
        glFogi(GL_FOG_MODE, GL_EXP)
        glFogf(GL_FOG_DENSITY, 2.0 if lava else 0.1)
        glFogfv(GL_FOG_COLOR, (float(fog[0]), float(fog[1]), float(fog[2]), 1.0))

    def setup_linear_fog(self):
        from OpenGL.GL import (
            GL_LINEAR, GL_FOG_MODE, GL_FOG_START, GL_FOG_END, GL_FOG, GL_FOG_COLOR,
            glFogi, glFogf, glFogfv, glEnable,
        )
        fog = getattr(self, "fog_color", (0.6, 0.8, 1.0))
        glEnable(GL_FOG)
        glFogi(GL_FOG_MODE, GL_LINEAR)
        glFogf(GL_FOG_START, self.far_clip * 0.25)
        glFogf(GL_FOG_END, self.far_clip)
        self.enable_alpha_radial_fog()
        glFogfv(GL_FOG_COLOR, (float(fog[0]), float(fog[1]), float(fog[2]), 1.0))

    def camera_relative_offset(self, world_x, world_y, world_z):
        return (
            float(world_x) - float(self.camX),
            float(world_y) - float(self.camY),
            float(world_z) - float(self.camZ),
        )

    def _calculate_normal(self, verts):
        v0 = np.array(verts[0])
        v1 = np.array(verts[1])
        v2 = np.array(verts[2])
        edge1 = v1 - v0
        edge2 = v2 - v0
        normal = np.cross(edge1, edge2)
        return normal / np.linalg.norm(normal)

    def _get_or_create_display_list(self, vertices, faces, color, texture=None, tex_coords=None):
        cache_key = (id(vertices), id(faces), tuple(color), id(texture) if texture else None)

        if cache_key in self.mesh_cache:
            return self.mesh_cache[cache_key]

        display_list = glGenLists(1)
        glNewList(display_list, GL_COMPILE)

        nr, ng, nb = self._norm_color(color)

        if texture:
            glEnable(GL_TEXTURE_2D)
            texture.bind()

        glBegin(GL_TRIANGLES)
        for face_idx, face in enumerate(faces):
            face_verts = [vertices[idx] for idx in face]

            glColor3f(nr, ng, nb)

            for i, vert in enumerate(face_verts):
                if texture and tex_coords and face_idx * 3 + i < len(tex_coords):
                    u, v = tex_coords[face_idx * 3 + i]
                    glTexCoord2f(u, v)
                glVertex3f(*vert)

        glEnd()

        if texture:
            texture.unbind()
            glDisable(GL_TEXTURE_2D)

        glEndList()

        self.mesh_cache[cache_key] = display_list
        return display_list

    def draw_mesh(self, vertices, faces, position, rotation, size, color, texture=None, tex_coords=None):
        glPushMatrix()

        glTranslatef(*position)
        glRotatef(rotation[1], 0, 1, 0)  
        glRotatef(rotation[0], 1, 0, 0)  
        glRotatef(rotation[2], 0, 0, 1)  
        glScalef(size[0] / 2, size[1] / 2, size[2] / 2)

        display_list = self._get_or_create_display_list(vertices, faces, color, texture, tex_coords)
        glCallList(display_list)

        glPopMatrix()

    def poly_3d(self, verts, color):
        glBegin(GL_POLYGON)
        nr, ng, nb = self._norm_color(color)

        glColor3f(nr, ng, nb)

        for vert in verts:
            glVertex3f(*vert)
        glEnd()

    def point_3d(self, pos, color):
        glBegin(GL_POINTS)
        nr, ng, nb = self._norm_color(color)
        glColor3f(nr, ng, nb)
        glVertex3f(*pos)
        glEnd()

    def line_3d(self, start, end, color):
        glBegin(GL_LINES)
        nr, ng, nb = self._norm_color(color)
        glColor3f(nr, ng, nb)
        glVertex3f(*start)
        glVertex3f(*end)
        glEnd()

    def quad_3d(self, pos, rot, scale, color, texture=None, cull_backface=True):
        glPushMatrix()
        glTranslatef(*pos)
        glRotatef(rot[0], 1, 0, 0)
        glRotatef(rot[1], 0, 1, 0)
        glRotatef(rot[2], 0, 0, 1)
        glScalef(scale[0], 1, scale[1])

        if cull_backface:
            glEnable(GL_CULL_FACE)
        else:
            glDisable(GL_CULL_FACE)

        if texture:
            glEnable(GL_TEXTURE_2D)
            texture.bind()

        glBegin(GL_QUADS)
        nr, ng, nb = self._norm_color(color)
        glColor3f(nr, ng, nb)

        if texture:
            glTexCoord2f(0, 0)
        glVertex3f(-0.5, -0.5, 0)

        if texture:
            glTexCoord2f(1, 0)
        glVertex3f(0.5, -0.5, 0)

        if texture:
            glTexCoord2f(1, 1)
        glVertex3f(0.5, 0.5, 0)

        if texture:
            glTexCoord2f(0, 1)
        glVertex3f(-0.5, 0.5, 0)

        glEnd()

        if texture:
            texture.unbind()
            glDisable(GL_TEXTURE_2D)

        glPopMatrix()

    def text_3d(self, text, pos, color, scale=1):
        if not text:
            return

        font = pg.font.Font("resources/lucon.ttf", 48)

        surf = font.render(text, True, (255, 255, 255))
        w, h = surf.get_size()
        text_data = pg.image.tostring(surf, "RGBA", True)

        nr, ng, nb = self._norm_color(color)

        glPushMatrix()

        glTranslatef(*pos)
        glScalef(scale, scale, scale)
        glTranslatef(0, -h, 0)

        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

        glEnable(GL_TEXTURE_2D)

        tex = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, tex)

        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)

        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0,
                     GL_RGBA, GL_UNSIGNED_BYTE, text_data)

        glColor3f(nr, ng, nb)

        glDisable(GL_CULL_FACE)
        glBegin(GL_QUADS)
        glTexCoord2f(0, 0);
        glVertex3f(0, 0, 0)
        glTexCoord2f(1, 0);
        glVertex3f(w, 0, 0)
        glTexCoord2f(1, 1);
        glVertex3f(w, h, 0)
        glTexCoord2f(0, 1);
        glVertex3f(0, h, 0)
        glEnd()

        glDeleteTextures([tex])

        glDisable(GL_TEXTURE_2D)
        glDisable(GL_BLEND)
        glEnable(GL_CULL_FACE)

        glPopMatrix()

    def transform_camera(self):
        glRotatef(-self.pitch, 1, 0, 0)
        glRotatef(-self.yaw, 0, 1, 0)
        glRotatef(-self.roll, 0, 0, 1)