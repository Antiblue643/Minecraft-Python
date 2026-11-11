import json

from .common import MOD_DIR
from game.gameplay.crafting import CraftingManager

def register_recipes():
    recipe_path = MOD_DIR / "data" / "recipes.json"
    if not recipe_path.is_file():
        return
    try:
        data = json.loads(recipe_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    manager = CraftingManager.get_instance()
    for name, spec in data.items():
        if not isinstance(spec, dict):
            continue
        if any(getattr(r, "name", None) == name for r in manager.recipes):
            continue
        recipe = CraftingManager._build(name, {
            **spec,
            "result": spec.get("result", name),
        })
        if recipe is not None:
            manager.recipes.append(recipe)
    manager.recipes.sort(key=lambda r: -r.size)
