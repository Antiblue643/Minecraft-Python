from __future__ import annotations

from dataclasses import dataclass

from OpenGL.GL import (
    GL_COMPILE_STATUS,
    GL_FOG_COLOR,
    GL_FOG_DENSITY,
    GL_FOG_END,
    GL_FOG_MODE,
    GL_FOG_START,
    GL_FRAGMENT_SHADER,
    GL_LINK_STATUS,
    GL_VERTEX_SHADER,
    glAttachShader,
    glCompileShader,
    glCreateProgram,
    glCreateShader,
    glDeleteProgram,
    glDeleteShader,
    glGetProgramInfoLog,
    glGetProgramiv,
    glGetFloatv,
    glGetIntegerv,
    glGetShaderInfoLog,
    glGetShaderiv,
    glGetUniformLocation,
    glLinkProgram,
    glShaderSource,
    glUniform1f,
    glUniform1fv,
    glUniform1i,
    glUniform4f,
    glUseProgram,
)


from engine.lighting import LIGHT_BRIGHTNESS_TABLE


@dataclass(frozen=True)
class GreedyFace:
    direction: tuple[int, int, int]
    plane: int
    u: int
    v: int
    block_id: int
    atlas_index: int
    color: tuple[float, float, float]
    sky_light: int = 15
    block_light: int = 0


@dataclass(frozen=True)
class GreedyQuad:
    direction: tuple[int, int, int]
    plane: int
    u: int
    v: int
    width: int
    height: int
    block_id: int
    atlas_index: int
    color: tuple[float, float, float]
    sky_light: int = 15
    block_light: int = 0


def merge_greedy_faces(faces: list[GreedyFace]) -> list[GreedyQuad]:
    groups: dict[tuple, tuple[int, set[tuple[int, int]]]] = {}
    for face in faces:
        key = (
            face.direction, face.plane, face.atlas_index, face.color,
            int(face.sky_light), int(face.block_light),
        )
        group = groups.get(key)
        if group is None:
            group = (face.block_id, set())
            groups[key] = group
        group[1].add((face.u, face.v))

    merged = []
    for key, (block_id, cells) in groups.items():
        direction, plane, atlas_index, color, sky_light, block_light = key
        ordered_cells = sorted(cells, key=lambda cell: (cell[1], cell[0]))
        next_cell = 0
        while next_cell < len(ordered_cells):
            u0, v0 = ordered_cells[next_cell]
            next_cell += 1
            if (u0, v0) not in cells:
                continue
            width = 1
            while (u0 + width, v0) in cells:
                width += 1

            height = 1
            while all((u, v0 + height) in cells for u in range(u0, u0 + width)):
                height += 1

            for v in range(v0, v0 + height):
                for u in range(u0, u0 + width):
                    cells.remove((u, v))
            merged.append(GreedyQuad(
                direction, plane, u0, v0, width, height, block_id,
                atlas_index, color, sky_light, block_light,
            ))
    return merged


def greedy_quad_geometry(quad: GreedyQuad, origin_x=0, origin_z=0):
    dx, dy, dz = quad.direction
    plane = quad.plane / 2.0
    u, v, width, height = quad.u, quad.v, quad.width, quad.height
    if dx == 1:
        x = plane
        y0, y1 = v - 0.5, v + height - 0.5
        z0, z1 = -u - width + 0.5, -u + 0.5
        vertices = ((x, y0, z1), (x, y0, z0), (x, y1, z0), (x, y1, z1))
    elif dx == -1:
        x = plane
        y0, y1 = v - 0.5, v + height - 0.5
        z0, z1 = u - 0.5, u + width - 0.5
        vertices = ((x, y0, z0), (x, y0, z1), (x, y1, z1), (x, y1, z0))
    elif dy == 1:
        y = plane
        x0, x1 = u - 0.5, u + width - 0.5
        z0, z1 = -v - height + 0.5, -v + 0.5
        vertices = ((x0, y, z1), (x1, y, z1), (x1, y, z0), (x0, y, z0))
    elif dy == -1:
        y = plane
        x0, x1 = u - 0.5, u + width - 0.5
        z0, z1 = v - 0.5, v + height - 0.5
        vertices = ((x0, y, z0), (x1, y, z0), (x1, y, z1), (x0, y, z1))
    elif dz == 1:
        z = plane
        x0, x1 = u - 0.5, u + width - 0.5
        y0, y1 = v - 0.5, v + height - 0.5
        vertices = ((x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z))
    else:
        z = plane
        x0, x1 = -u - width + 0.5, -u + 0.5
        y0, y1 = v - 0.5, v + height - 0.5
        vertices = ((x1, y0, z), (x0, y0, z), (x0, y1, z), (x1, y1, z))

    vertices = tuple(
        (x - origin_x, y, z - origin_z) for x, y, z in vertices
    )
    uvs = ((0.0, 0.0), (float(width), 0.0),
           (float(width), float(height)), (0.0, float(height)))
    return vertices, uvs


_VERTEX_SHADER = """#version 120
varying vec2 tile_uv;
varying vec4 vertex_color;
uniform int radial_fog;
varying float fog_distance;
varying vec2 atlas_origin;
varying vec2 raw_light;
void main() {
    vec4 eye_position = gl_ModelViewMatrix * gl_Vertex;
    gl_Position = gl_ProjectionMatrix * eye_position;
    tile_uv = gl_MultiTexCoord0.xy;
    atlas_origin = gl_MultiTexCoord1.xy;
    raw_light = gl_MultiTexCoord1.zw;
    vertex_color = gl_Color;
    fog_distance = radial_fog != 0
        ? length(eye_position.xyz)
        : abs(eye_position.z);
}
"""

_FRAGMENT_SHADER = """#version 120
uniform sampler2D atlas;
uniform vec4 fog_color;
uniform int fog_mode;
uniform float fog_start;
uniform float fog_end;
uniform float fog_density;
uniform float skylight_subtracted;
uniform float light_brightness[16];
varying vec2 tile_uv;
varying vec2 atlas_origin;
varying vec2 raw_light;
varying vec4 vertex_color;
varying float fog_distance;
void main() {
vec2 repeated_uv = atlas_origin + fract(tile_uv) / 16.0;
    vec4 material = texture2D(atlas, repeated_uv) * vertex_color;
    if (material.a <= 0.5) discard;

    // The sky component is interpolated between game ticks at render time.
    // Block light remains independent, matching max(sky - subtraction, block).
    float effective_sky = max(0.0, raw_light.x - skylight_subtracted);
    float combined_light = clamp(max(effective_sky, raw_light.y), 0.0, 15.0);
    int lower_level = int(floor(combined_light));
    int upper_level = lower_level < 15 ? lower_level + 1 : 15;
    float blend = combined_light - float(lower_level);
    float brightness = mix(
        light_brightness[lower_level], light_brightness[upper_level], blend
    );
    material.rgb *= brightness;
    float fog_factor;
    if (fog_mode == 9729) {
        // GL_LINEAR
        fog_factor = clamp(
            (fog_end - fog_distance) / (fog_end - fog_start), 0.0, 1.0
        );
    } else if (fog_mode == 2048) {
        // GL_EXP
        fog_factor = clamp(exp(-fog_density * fog_distance), 0.0, 1.0);
    } else {
        // GL_EXP2 (2049)
        float density_distance = fog_density * fog_distance;
        fog_factor = clamp(exp(-density_distance * density_distance), 0.0, 1.0);
    }
    gl_FragColor = vec4(
        mix(fog_color.rgb, material.rgb, fog_factor), material.a
    );
}
"""

_greedy_program = None


def _shader_log(get_log, shader_or_program):
    try:
        return get_log(shader_or_program)
    except Exception:
        return b""


def get_greedy_shader():
    global _greedy_program
    if _greedy_program:
        return _greedy_program

    shaders = []
    try:
        for shader_type, source in (
            (GL_VERTEX_SHADER, _VERTEX_SHADER),
            (GL_FRAGMENT_SHADER, _FRAGMENT_SHADER),
        ):
            shader = glCreateShader(shader_type)
            shaders.append(shader)
            glShaderSource(shader, source)
            glCompileShader(shader)
            if not glGetShaderiv(shader, GL_COMPILE_STATUS):
                raise RuntimeError(
                    "Greedy mesh shader compilation failed: "
                    + str(_shader_log(glGetShaderInfoLog, shader))
                )

        program = glCreateProgram()
        for shader in shaders:
            glAttachShader(program, shader)
        glLinkProgram(program)
        if not glGetProgramiv(program, GL_LINK_STATUS):
            log = _shader_log(glGetProgramInfoLog, program)
            glDeleteProgram(program)
            raise RuntimeError("Greedy mesh shader link failed: " + str(log))
        _greedy_program = int(program)
        return _greedy_program
    finally:
        for shader in shaders:
            glDeleteShader(shader)


def bind_greedy_shader(*, radial_fog=False, skylight_subtracted=0.0):
    program = get_greedy_shader()
    glUseProgram(program)
    sampler = glGetUniformLocation(program, "atlas")
    if sampler >= 0:
        glUniform1i(sampler, 0)
    fog_mode_value = int(glGetIntegerv(GL_FOG_MODE))
    fog_mode = glGetUniformLocation(program, "fog_mode")
    if fog_mode >= 0:
        glUniform1i(fog_mode, fog_mode_value)
    sky_sub = glGetUniformLocation(program, "skylight_subtracted")
    if sky_sub >= 0:
        glUniform1f(sky_sub, float(skylight_subtracted))
    brightness = glGetUniformLocation(program, "light_brightness[0]")
    if brightness >= 0:
        glUniform1fv(brightness, len(LIGHT_BRIGHTNESS_TABLE), LIGHT_BRIGHTNESS_TABLE)
    radial = glGetUniformLocation(program, "radial_fog")
    if radial >= 0:
        glUniform1i(radial, int(bool(radial_fog)))
    for name, parameter in (
        ("fog_start", GL_FOG_START),
        ("fog_end", GL_FOG_END),
        ("fog_density", GL_FOG_DENSITY),
    ):
        location = glGetUniformLocation(program, name)
        if location >= 0:
            glUniform1f(location, float(glGetFloatv(parameter)))
    fog_color = glGetUniformLocation(program, "fog_color")
    if fog_color >= 0:
        color = glGetFloatv(GL_FOG_COLOR)
        glUniform4f(fog_color, *(float(component) for component in color))
    return program


def unbind_greedy_shader():
    glUseProgram(0)
