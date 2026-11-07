from game.entities.item import Item, ItemStack


class Inventory:
    HOTBAR_SIZE = 9
    MAIN_SIZE = 36
    ARMOR_SIZE = 4
    STACK_LIMIT = 64

    def __init__(self, player=None):
        self.player = player
        self.main = [None] * self.MAIN_SIZE
        self.armor = [None] * self.ARMOR_SIZE
        self.current_item = 0

    @property
    def size(self):
        return self.MAIN_SIZE + self.ARMOR_SIZE

    @property
    def stack_limit(self):
        return self.STACK_LIMIT

    def _resolve(self, slot):
        if slot >= self.MAIN_SIZE:
            return self.armor, slot - self.MAIN_SIZE
        return self.main, slot

    def get_stack(self, slot):
        if not 0 <= slot < self.size:
            return None
        array, index = self._resolve(slot)
        return array[index]

    def set_stack(self, slot, stack):
        if not 0 <= slot < self.size:
            return
        array, index = self._resolve(slot)
        array[index] = stack

    def decr_stack_size(self, slot, amount):
        if not 0 <= slot < self.size:
            return None
        array, index = self._resolve(slot)
        stack = array[index]
        if stack is None:
            return None
        if stack.count <= amount:
            array[index] = None
            return stack
        taken = stack.split(amount)
        if stack.count == 0:
            array[index] = None
        return taken

    def get_active_item(self):
        return self.main[self.current_item]

    def get_damage_vs_entity(self, entity=None):
        stack = self.get_active_item()
        if stack is None:
            return 1
        from game.entities.combat import item_damage_vs_entity
        from game.entities.item import Item
        return item_damage_vs_entity(Item.definition(stack.id))

    @property
    def active_idx(self):
        return self.current_item

    def _notify_hotbar_selection(self):
        callback = getattr(self, "_hotbar_selection_callback", None)
        if callable(callback):
            try:
                callback(int(self.current_item))
            except Exception:
                pass

    def scroll(self, direction):
        step = 1 if direction > 0 else (-1 if direction < 0 else 0)
        if step:
            self.select_hotbar((self.current_item + step) % self.HOTBAR_SIZE)

    def select_hotbar(self, slot):
        if 0 <= slot < self.HOTBAR_SIZE and int(slot) != self.current_item:
            self.current_item = int(slot)
            self._notify_hotbar_selection()

    def hotbar_items(self):
        return self.main[:self.HOTBAR_SIZE]

    def hotbar_selected_local(self):
        return self.current_item

    def consume_current(self, amount=1):
        stack = self.main[self.current_item]
        if stack is None:
            return False
        stack.count -= amount
        if stack.count <= 0:
            self.main[self.current_item] = None
        return True

    def consume_inventory_item(self, item_id, amount=1):
        remaining = max(0, int(amount))
        if remaining == 0:
            return True
        for slot, stack in enumerate(self.main):
            if stack is None or stack.id != item_id:
                continue
            take = min(remaining, stack.count)
            stack.count -= take
            remaining -= take
            if stack.count <= 0:
                self.main[slot] = None
            if remaining <= 0:
                return True
        return False

    def remove_item(self, slot):
        return self.decr_stack_size(slot, 1)

    def clear_if_empty(self, slot):
        stack = self.get_stack(slot)
        if stack is not None and stack.count <= 0:
            self.set_stack(slot, None)

    def _find_stack_with_room(self, item_id, damage=0):
        for i, stack in enumerate(self.main):
            if (stack is not None and stack.id == item_id
                    and int(stack.damage) == int(damage)
                    and stack.count < stack.max_stack_size
                    and stack.count < self.STACK_LIMIT):
                return i
        return -1

    def _first_empty(self):
        for i, stack in enumerate(self.main):
            if stack is None:
                return i
        return -1

    def _store_partial(self, item_id, count, damage=0):
        slot = self._find_stack_with_room(item_id, damage)
        if slot < 0:
            slot = self._first_empty()
        if slot < 0:
            return count
        if self.main[slot] is None:
            self.main[slot] = ItemStack(item_id, 0, damage)
        target = self.main[slot]
        room = min(target.max_stack_size - target.count, self.STACK_LIMIT - target.count)
        moved = min(count, room)
        if moved <= 0:
            return count
        target.count += moved
        target.animations_to_go = 5
        return count - moved

    def add_stack(self, stack):
        if stack.damage == 0 or Item.has_subtypes(stack.id):
            while stack.count > 0:
                left = self._store_partial(stack.id, stack.count, stack.damage)
                if left == stack.count:
                    break  
                stack.count = left
            if stack.count == 0:
                return True
        slot = self._first_empty()
        if slot >= 0:
            self.main[slot] = stack
            stack.animations_to_go = 5
            return True
        return False

    def decrement_animations(self):
        for stack in self.main:
            if stack is not None and stack.animations_to_go > 0:
                stack.animations_to_go -= 1

    def get_total_armor_value(self):
        from game.entities.item import Item

        reduction = 0
        remaining = 0
        maximum = 0
        slot_reduction = (3, 8, 6, 3)
        for stack in self.armor:
            if stack is None:
                continue
            armor_type = Item.armor_slot(stack.id)
            if armor_type is None or not 0 <= armor_type < len(slot_reduction):
                continue
            max_damage = stack.max_damage
            if max_damage <= 0:
                continue
            reduction += slot_reduction[armor_type]
            remaining += max(0, max_damage - max(0, stack.damage))
            maximum += max_damage

        if maximum <= 0 or reduction <= 0:
            return 0
        return max(0, min(20, ((reduction - 1) * remaining) // maximum + 1))

    def damage_armor(self, amount):
        if amount <= 0:
            return
        from game.entities.item import Item
        for i, stack in enumerate(self.armor):
            if stack is None:
                continue
            if Item.armor_slot(stack.id) is None:
                continue
            if stack.damage_item(int(amount)) or stack.count <= 0:
                self.armor[i] = None

    def drop_all_items(self):
        if self.player is None:
            return
        for array in (self.main, self.armor):
            for i, stack in enumerate(array):
                if stack is not None:
                    inventory_slot = i if array is self.main else 36 + i
                    self.player.drop_player_item(
                        stack, random_choice=True, inventory_slot=inventory_slot,
                    )
                    array[i] = None
