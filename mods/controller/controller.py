

def controller_grid_step(cursor, targets, dx, dy):
    points = [(float(x), float(y)) for x, y in targets]
    if not points:
        return None
    dx = 0 if not dx else (1 if dx > 0 else -1)
    dy = 0 if not dy else (1 if dy > 0 else -1)
    if dx == 0 and dy == 0:
        return None
    if dx and dy:
        if abs(float(dx)) >= abs(float(dy)):
            dy = 0
        else:
            dx = 0

    cx, cy = (float(cursor[0]), float(cursor[1])) if cursor is not None else points[0]
    current = min(range(len(points)), key=lambda i: (points[i][0] - cx) ** 2 + (points[i][1] - cy) ** 2)
    ox, oy = points[current]

    def projections(point):
        vx, vy = point[0] - ox, point[1] - oy
        forward = vx * dx + vy * dy
        lateral = abs(vx * dy - vy * dx)
        return forward, lateral

    forward_candidates = []
    wrap_candidates = []
    for index, point in enumerate(points):
        if index == current:
            continue
        forward, lateral = projections(point)
        if forward > 1.0:
            forward_candidates.append((lateral * 2.0 + forward * 0.15, forward, index))
        elif forward < -1.0:
            wrap_candidates.append((lateral, forward, index))

    if forward_candidates:
        selected = min(forward_candidates)[2]
    elif wrap_candidates:
        selected = min(wrap_candidates)[2]
    else:
        return points[current]
    return points[selected]


class ControllerState:
    __slots__ = (
        "move_x", "move_y", "look_x", "look_y", "left_trigger",
        "right_trigger", "buttons", "hat",
    )

    def __init__(self, move_x=0.0, move_y=0.0, look_x=0.0, look_y=0.0,
                 buttons=(), hat=(0, 0), left_trigger=0.0, right_trigger=0.0):
        self.move_x = float(move_x)
        self.move_y = float(move_y)
        self.look_x = float(look_x)
        self.look_y = float(look_y)
        self.left_trigger = max(0.0, min(1.0, float(left_trigger)))
        self.right_trigger = max(0.0, min(1.0, float(right_trigger)))
        self.buttons = frozenset(int(button) for button in buttons)
        self.hat = tuple(hat) if hat else (0, 0)

    @property
    def movement_axes(self):
        return _clamp_axis(self.move_x), _clamp_axis(self.move_y)


class ControllerManager:

    MOVE_DEADZONE = 0.18
    LOOK_DEADZONE = 0.14

    def __init__(self, pygame_module):
        self.pg = pygame_module
        self.joystick = None
        self._joystick_initialized = False
        self.last_state = ControllerState()
        self._initialize_subsystem()

    def _initialize_subsystem(self):
        if self._joystick_initialized:
            return
        try:
            self.pg.joystick.init()
            self._joystick_initialized = True
        except Exception:
            self._joystick_initialized = False

    def _close_device(self):
        joystick, self.joystick = self.joystick, None
        if joystick is not None:
            try:
                joystick.quit()
            except Exception:
                pass

    def _ensure_device(self):
        self._initialize_subsystem()
        if not self._joystick_initialized:
            return None

        if self.joystick is not None:
            try:
                attached = getattr(self.joystick, "get_attached", None)
                if not callable(attached) or attached():
                    return self.joystick
            except Exception:
                pass
            self._close_device()

        try:
            if self.pg.joystick.get_count() < 1:
                return None
            joystick = self.pg.joystick.Joystick(0)
            if not joystick.get_init():
                joystick.init()
            self.joystick = joystick
            return joystick
        except Exception:
            self._close_device()
            return None

    @staticmethod
    def _axis(joystick, index):
        try:
            if joystick.get_numaxes() <= index:
                return 0.0
            value = float(joystick.get_axis(index))
            if not (-1.0 <= value <= 1.0):
                return 0.0
            return value
        except Exception:
            return 0.0

    @staticmethod
    def _trigger(joystick, index):
        try:
            if joystick.get_numaxes() <= index:
                return 0.0
            value = float(joystick.get_axis(index))
            if not (-1.0 <= value <= 1.0):
                return 0.0
            return (value + 1.0) * 0.5 if value < 0.0 else value
        except Exception:
            return 0.0

    @classmethod
    def _radial_deadzone(cls, x, y, threshold):
        import math

        magnitude = math.hypot(x, y)
        if magnitude <= threshold:
            return 0.0, 0.0
        scale = min(1.0, (magnitude - threshold) / (1.0 - threshold)) / magnitude
        return x * scale, y * scale

    def poll(self):
        joystick = self._ensure_device()
        if joystick is None:
            self.last_state = ControllerState()
            return self.last_state

        lx = self._axis(joystick, 0)
        ly = self._axis(joystick, 1)
        rx = self._axis(joystick, 2)
        ry = self._axis(joystick, 3)
        move_x, move_y = self._radial_deadzone(
            lx, -ly, self.MOVE_DEADZONE,
        )
        look_x, look_y = self._radial_deadzone(
            rx, ry, self.LOOK_DEADZONE,
        )

        buttons = set()
        left_trigger = self._trigger(joystick, 4)
        right_trigger = self._trigger(joystick, 5)

        try:
            for index in range(max(0, int(joystick.get_numbuttons()))):
                if joystick.get_button(index):
                    buttons.add(index)
        except Exception:
            buttons.clear()

        hat = (0, 0)
        try:
            if joystick.get_numhats() > 0:
                hat = joystick.get_hat(0)
        except Exception:
            pass

        self.last_state = ControllerState(
            move_x, move_y, look_x, look_y, buttons, hat,
            left_trigger=left_trigger, right_trigger=right_trigger,
        )
        return self.last_state

    def handle_event(self, event):
        event_type = getattr(event, "type", None)
        added = getattr(self.pg, "JOYDEVICEADDED", None)
        removed = getattr(self.pg, "JOYDEVICEREMOVED", None)
        if event_type == added or event_type == removed:
            self._close_device()
            self._initialize_subsystem()
            self.poll()


def _clamp_axis(value):
    return max(-1.0, min(1.0, float(value)))


class ControllerKeyState:

    def __init__(self, keyboard_state, settings, controller_state, *,
                 allow_movement=True, allow_controller_jump=True,
                 controller_sneak_latched=False, sneak_toggle=True):
        self._keyboard_state = keyboard_state
        self._settings = settings
        self._controller_state = controller_state or ControllerState()
        self._allow_movement = bool(allow_movement)
        self._allow_controller_jump = bool(allow_controller_jump)
        self._controller_sneak_latched = bool(controller_sneak_latched)
        self._sneak_toggle = bool(sneak_toggle)

    @property
    def movement_axes(self):
        if not self._allow_movement:
            return 0.0, 0.0
        return self._controller_state.movement_axes

    def __getitem__(self, keycode):
        try:
            down = bool(self._keyboard_state[keycode])
        except (IndexError, KeyError, TypeError):
            down = False
        if down:
            return True

        if not self._allow_movement:
            return False
        jump = getattr(getattr(self._settings, "key_jump", None), "key", None)
        if (
            self._allow_controller_jump
            and keycode == jump
            and 0 in self._controller_state.buttons
        ):
            return True
        sneak = getattr(getattr(self._settings, "key_sneak", None), "key", None)
        if keycode == sneak:
            if self._sneak_toggle:
                return self._controller_sneak_latched
            return 9 in self._controller_state.buttons
        return False


def screen_supports_scroll(screen):
    if screen is None:
        return False
    if any(callable(getattr(screen, name, None)) for name in (
        "controller_scroll", "scroll_by", "handle_scroll", "mouse_scroll",
    )):
        return True
    for offset_name in ("scroll", "scroll_y", "scroll_offset"):
        if not hasattr(screen, offset_name):
            continue
        maximum = getattr(screen, "_max_scroll", None)
        if callable(maximum):
            return True
        maximum = getattr(screen, "max_scroll", None)
        if callable(maximum) or isinstance(maximum, (int, float)):
            return True
    return False


def scroll_screen(screen, delta_pixels):
    import math

    try:
        delta = float(delta_pixels)
    except (TypeError, ValueError):
        return False
    if screen is None or not math.isfinite(delta) or abs(delta) < 0.001:
        return False

    for name in ("controller_scroll", "scroll_by", "handle_scroll", "mouse_scroll"):
        callback = getattr(screen, name, None)
        if callable(callback):
            callback(delta)
            return True

    for offset_name in ("scroll", "scroll_y", "scroll_offset"):
        current = getattr(screen, offset_name, None)
        if current is None:
            continue
        maximum = getattr(screen, "_max_scroll", None)
        if callable(maximum):
            limit = maximum()
        else:
            limit = getattr(screen, "max_scroll", None)
            if callable(limit):
                limit = limit()
        if not isinstance(limit, (int, float)):
            continue
        target = max(0, min(int(limit), int(round(float(current) + delta))))
        setter = getattr(screen, "_set_scroll", None)
        if not callable(setter):
            setter = getattr(screen, "set_scroll", None)
        if callable(setter):
            setter(target)
        else:
            setattr(screen, offset_name, target)
        return True
    return False
