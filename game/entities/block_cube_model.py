_HALF = 0.5

BLOCK_CUBE_FACES = (
    ("bottom", (0, -1, 0), 0.5,
     ((-_HALF, -_HALF, _HALF), (-_HALF, -_HALF, -_HALF),
      (_HALF, -_HALF, -_HALF), (_HALF, -_HALF, _HALF))),
    ("top", (0, 1, 0), 1.0,
     ((-_HALF, _HALF, -_HALF), (-_HALF, _HALF, _HALF),
      (_HALF, _HALF, _HALF), (_HALF, _HALF, -_HALF))),
    ("back", (0, 0, -1), 0.8,
     ((_HALF, _HALF, -_HALF), (_HALF, -_HALF, -_HALF),
      (-_HALF, -_HALF, -_HALF), (-_HALF, _HALF, -_HALF))),
    ("front", (0, 0, 1), 0.8,
     ((-_HALF, _HALF, _HALF), (-_HALF, -_HALF, _HALF),
      (_HALF, -_HALF, _HALF), (_HALF, _HALF, _HALF))),
    ("left", (-1, 0, 0), 0.6,
     ((-_HALF, _HALF, -_HALF), (-_HALF, -_HALF, -_HALF),
      (-_HALF, -_HALF, _HALF), (-_HALF, _HALF, _HALF))),
    ("right", (1, 0, 0), 0.6,
     ((_HALF, _HALF, _HALF), (_HALF, -_HALF, _HALF),
      (_HALF, -_HALF, -_HALF), (_HALF, _HALF, -_HALF))),
)

FALLING_BLOCK_FACE_SLOTS = {
    "bottom": 0, "top": 1, "back": 2, "front": 3,
    "left": 4, "right": 5,
}
TNT_FACE_SLOTS = {
    "bottom": 2, "top": 3, "back": 5, "front": 4,
    "left": 1, "right": 0,
}
