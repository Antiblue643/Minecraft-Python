
from __future__ import annotations

import time

MAX_LINES = 50
MAX_LINE_WIDTH = 320
HUD_LINES = 10
HUD_LINES_CHAT_OPEN = 20
VISIBLE_TICKS = 200
TICKS_PER_SECOND = 20.0


def chat_line_alpha(age_ticks: float, chat_open: bool = False) -> int:
    if chat_open:
        return 255
    if age_ticks >= VISIBLE_TICKS:
        return 0
    fade = 1.0 - age_ticks / float(VISIBLE_TICKS)
    fade = max(0.0, min(1.0, fade * 10.0))
    return int(255.0 * fade * fade)


class ChatLine:
    __slots__ = ("message", "born")

    def __init__(self, message: str, born: float):
        self.message = message
        self.born = born


class ChatLog:

    def __init__(self, measure=None, clock=time.monotonic):
        self._measure = measure or (lambda text: len(text) * 6)
        self._clock = clock
        self._lines: list[ChatLine] = []

    def set_measure(self, measure) -> None:
        self._measure = measure

    def clear(self) -> None:
        self._lines.clear()

    def __len__(self) -> int:
        return len(self._lines)

    @property
    def lines(self) -> list[ChatLine]:
        return self._lines

    def add(self, text: str) -> None:
        text = str(text)
        while self._measure(text) > MAX_LINE_WIDTH:
            split = 1
            while split < len(text) and self._measure(text[:split + 1]) <= MAX_LINE_WIDTH:
                split += 1
            self.add(text[:split])
            text = text[split:]
        self._lines.insert(0, ChatLine(text, self._clock()))
        del self._lines[MAX_LINES:]

    def age_ticks(self, line: ChatLine) -> float:
        return max(0.0, (self._clock() - line.born) * TICKS_PER_SECOND)

    def visible(self, chat_open: bool = False):
        limit = HUD_LINES_CHAT_OPEN if chat_open else HUD_LINES
        rows = []
        for row, line in enumerate(self._lines[:limit]):
            alpha = chat_line_alpha(self.age_ticks(line), chat_open)
            if alpha > 0:
                rows.append((row, line, alpha))
        return rows
