
from contextlib import contextmanager


@contextmanager
def smooth_vertex_colors():
    from OpenGL.GL import GL_SHADE_MODEL, GL_SMOOTH, glGetIntegerv, glShadeModel

    previous_mode = int(glGetIntegerv(GL_SHADE_MODEL))
    glShadeModel(GL_SMOOTH)
    try:
        yield
    finally:
        glShadeModel(previous_mode)
