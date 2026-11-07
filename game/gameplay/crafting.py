import json
from pathlib import Path

from game.entities.item import Item, ItemStack

RECIPES_PATH = Path(__file__).resolve().parents[1] / "data" / "recipes.json"
GRID = 3


class CraftingRecipe:
    def __init__(self, result_id, count, width, height, pattern, *,
                 shapeless=False, name=None):
        self.result_id = result_id
        self.count = int(count)
        self.width = width
        self.height = height
        self.pattern = pattern
        self.shapeless = shapeless
        self.name = name or result_id
        self.ingredients = sorted(i for i in pattern if i is not None)

    @property
    def size(self):
        return len(self.ingredients) if self.shapeless else self.width * self.height

    @staticmethod
    def _ingredient_matches(wanted, actual):
        if wanted == actual:
            return True
        if wanted is None or actual is None:
            return False
        wanted_id, wanted_damage = Item.parse_subtype_key(wanted)
        actual_id, actual_damage = Item.parse_subtype_key(actual)
        if wanted_id != actual_id:
            return False
        if ":" not in str(wanted):
            return True
        return wanted_damage == actual_damage

    def matches(self, cells):
        if self.shapeless:
            remaining = [cell for cell in cells if cell is not None]
            if len(remaining) != len(self.ingredients):
                return False
            for wanted in self.ingredients:
                for index, actual in enumerate(remaining):
                    if self._ingredient_matches(wanted, actual):
                        remaining.pop(index)
                        break
                else:
                    return False
            return not remaining
        for ox in range(GRID - self.width + 1):
            for oy in range(GRID - self.height + 1):
                if (self._check(cells, ox, oy, True)
                        or self._check(cells, ox, oy, False)):
                    return True
        return False

    def _check(self, cells, ox, oy, mirrored):
        for x in range(GRID):
            for y in range(GRID):
                px, py = x - ox, y - oy
                want = None
                if 0 <= px < self.width and 0 <= py < self.height:
                    col = self.width - px - 1 if mirrored else px
                    want = self.pattern[col + py * self.width]
                if not self._ingredient_matches(want, cells[x + y * GRID]):
                    return False
        return True

    def create_result(self):
        result_id, damage = Item.parse_subtype_key(self.result_id)
        return ItemStack(result_id, self.count, damage)


class CraftingManager:
    _instance = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self, path=RECIPES_PATH):
        self.recipes = []
        self.furnace_recipes = []
        self._load(Path(path))

    def _load(self, path):
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        for name, spec in data.get("recipes", {}).items():
            if spec.get("station", "crafting") != "crafting":
                key = spec.get("key", {})
                input_id = key.get("x") or (next(iter(key.values())) if key else None)
                if input_id:
                    result_id = spec.get("result", name)
                    amount = int(spec.get("amount", 1))
                    self.furnace_recipes.append((input_id, result_id, amount))
                continue
            recipe = self._build(name, spec)
            if recipe is not None:
                self.recipes.append(recipe)
        self.recipes.sort(key=lambda r: -r.size)

    @staticmethod
    def _build(name, spec):
        rows = list(spec.get("shape") or [])
        if not rows:
            return None
        key = spec.get("key", {})
        width = max(len(row) for row in rows)
        if len(rows) > GRID or width > GRID:
            return None
        pattern = []
        for row in rows:
            for col in range(width):
                ch = row[col] if col < len(row) else "#"
                pattern.append(key.get(ch) if ch != "#" else None)
        return CraftingRecipe(
            spec.get("result", name), spec.get("amount", 1), width, len(rows),
            pattern, shapeless=bool(spec.get("shapeless")), name=name,
        )

    def find_matching_recipe(self, cells):
        if all(c is None for c in cells):
            return None
        for recipe in self.recipes:
            if recipe.matches(cells):
                return recipe.create_result()
        return None


class CraftingGrid:
    stack_limit = 64

    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.stacks = [None] * (width * height)

    @property
    def size(self):
        return len(self.stacks)

    def get_stack(self, slot):
        return self.stacks[slot] if 0 <= slot < self.size else None

    def set_stack(self, slot, stack):
        if 0 <= slot < self.size:
            self.stacks[slot] = stack

    def cells(self):
        cells = [None] * (GRID * GRID)
        for y in range(self.height):
            for x in range(self.width):
                stack = self.stacks[x + y * self.width]
                if stack is not None and stack.count > 0:
                    cells[x + y * GRID] = stack.subtype_key
        return cells

    def consume_one_each(self):
        for i, stack in enumerate(self.stacks):
            if stack is not None:
                stack.count -= 1
                if stack.count <= 0:
                    container = Item.definition(stack.id).get("container")
                    if container:
                        try:
                            self.stacks[i] = type(stack)(container, 1, 0)
                        except Exception:
                            self.stacks[i] = ItemStack(container, 1, 0)
                    else:
                        self.stacks[i] = None

    def take_all(self):
        taken = [s for s in self.stacks if s is not None]
        self.stacks = [None] * self.size
        return taken


class CraftResult:
    stack_limit = 64
    size = 1

    def __init__(self):
        self.stack = None

    def get_stack(self, slot=0):
        return self.stack if slot == 0 else None

    def set_stack(self, slot, stack):
        if slot == 0:
            self.stack = stack
