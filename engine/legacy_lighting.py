
from OpenGL.GL import (
    GL_AMBIENT,
    GL_COLOR_MATERIAL,
    GL_DIFFUSE,
    GL_FLAT,
    GL_FRONT_AND_BACK,
    GL_LIGHT0,
    GL_LIGHT1,
    GL_LIGHTING,
    GL_LIGHT_MODEL_AMBIENT,
    GL_POSITION,
    GL_SPECULAR,
    GL_AMBIENT_AND_DIFFUSE,
    glColorMaterial,
    glDisable,
    glEnable,
    glLightModelfv,
    glLightfv,
    glShadeModel,
)


def _normalized(vector):
    length = sum(component * component for component in vector) ** 0.5
    return tuple(component / length for component in vector)


def enable_standard_item_lighting():
    glEnable(GL_LIGHTING)
    glEnable(GL_LIGHT0)
    glEnable(GL_LIGHT1)
    glEnable(GL_COLOR_MATERIAL)
    glColorMaterial(GL_FRONT_AND_BACK, GL_AMBIENT_AND_DIFFUSE)

    diffuse = (0.6, 0.6, 0.6, 1.0)
    ambient = (0.0, 0.0, 0.0, 1.0)
    specular = (0.0, 0.0, 0.0, 1.0)
    glLightfv(GL_LIGHT0, GL_POSITION, (*_normalized((0.7, 1.0, -0.2)), 0.0))
    glLightfv(GL_LIGHT0, GL_DIFFUSE, diffuse)
    glLightfv(GL_LIGHT0, GL_AMBIENT, ambient)
    glLightfv(GL_LIGHT0, GL_SPECULAR, specular)

    glLightfv(GL_LIGHT1, GL_POSITION, (*_normalized((-0.7, 1.0, 0.2)), 0.0))
    glLightfv(GL_LIGHT1, GL_DIFFUSE, diffuse)
    glLightfv(GL_LIGHT1, GL_AMBIENT, ambient)
    glLightfv(GL_LIGHT1, GL_SPECULAR, specular)
    glShadeModel(GL_FLAT)
    glLightModelfv(GL_LIGHT_MODEL_AMBIENT, (0.4, 0.4, 0.4, 1.0))


def disable_standard_item_lighting():
    glDisable(GL_LIGHTING)
    glDisable(GL_LIGHT0)
    glDisable(GL_LIGHT1)
    glDisable(GL_COLOR_MATERIAL)
