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


class MouseFilter:

    def __init__(self):
        self.accumulated = 0.0
        self.last_filtered = 0.0
        self.smoothed_delta = 0.0

    def reset(self):
        self.accumulated = 0.0
        self.last_filtered = 0.0
        self.smoothed_delta = 0.0

    def filter(self, value, smoothing):
        self.accumulated += float(value)
        delta = (self.accumulated - self.last_filtered) * float(smoothing)
        self.smoothed_delta += (delta - self.smoothed_delta) * 0.5
        if ((delta > 0.0 and delta > self.smoothed_delta)
                or (delta < 0.0 and delta < self.smoothed_delta)):
            delta = self.smoothed_delta
        self.last_filtered += delta
        return delta
