from __future__ import annotations

from pathlib import Path

OVERWORLD_DIMENSION = 0
NETHER_DIMENSION = -1


def scale_horizontal_coordinates(x: float, z: float, source_dimension: int,
                                 target_dimension: int) -> tuple[float, float]:
    source_dimension = int(source_dimension)
    target_dimension = int(target_dimension)
    if source_dimension not in (OVERWORLD_DIMENSION, NETHER_DIMENSION):
        raise ValueError(f"Unsupported source dimension: {source_dimension}")
    if target_dimension not in (OVERWORLD_DIMENSION, NETHER_DIMENSION):
        raise ValueError(f"Unsupported target dimension: {target_dimension}")
    x, z = float(x), float(z)
    if source_dimension == target_dimension:
        return x, z
    if source_dimension == OVERWORLD_DIMENSION:
        return x / 8.0, z / 8.0
    return x * 8.0, z * 8.0


def dimension_save_directory(world_directory: str | Path, dimension_id: int) -> Path:
    root = Path(world_directory)
    dimension_id = int(dimension_id)
    if dimension_id == OVERWORLD_DIMENSION:
        return root
    if dimension_id == NETHER_DIMENSION:
        return root / "DIM-1"
    raise ValueError(f"Unsupported dimension id: {dimension_id}")
