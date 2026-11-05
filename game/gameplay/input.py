class MovementFilteredKeyState:

    def __init__(self, key_states, blocked_keycodes):
        self._key_states = key_states
        self._blocked_keycodes = frozenset(
            int(key) for key in blocked_keycodes if isinstance(key, int)
        )

    def __getitem__(self, keycode):
        if keycode in self._blocked_keycodes:
            return False
        return self._key_states[keycode]

    @property
    def movement_axes(self):
        return 0.0, 0.0
