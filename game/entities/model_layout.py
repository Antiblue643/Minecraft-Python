
CREEPER_LEG_PIVOT_Y = 16
CREEPER_LEG_HEIGHT = 6
CREEPER_MODEL_FOOT_Y = CREEPER_LEG_PIVOT_Y + CREEPER_LEG_HEIGHT


def model_vertical_baseline(model_kind: str) -> float:
    return CREEPER_MODEL_FOOT_Y if model_kind == "creeper" else 24.0
