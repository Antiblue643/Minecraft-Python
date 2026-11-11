
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


CHAT_MAX_LENGTH = 100
CHAT_HISTORY_LIMIT = 100


def _is_word_char(char: str) -> bool:
    return char.isalnum() or char == "_"


class ChatInput:

    def __init__(self, history=None, max_length: int = CHAT_MAX_LENGTH):
        self.max_length = int(max_length)
        self.history = history if history is not None else []
        self.text = ""
        self.caret = 0
        self._history_index = None
        self._draft = ""
        self._completion = None

    def _edited(self) -> None:
        self._completion = None
        self._history_index = None

    def insert(self, text) -> None:
        clean = "".join(ch for ch in str(text) if ch.isprintable())
        room = self.max_length - len(self.text)
        if room <= 0 or not clean:
            return
        clean = clean[:room]
        self.text = self.text[:self.caret] + clean + self.text[self.caret:]
        self.caret += len(clean)
        self._edited()

    def backspace(self) -> None:
        if self.caret > 0:
            self.text = self.text[:self.caret - 1] + self.text[self.caret:]
            self.caret -= 1
            self._edited()

    def delete(self) -> None:
        if self.caret < len(self.text):
            self.text = self.text[:self.caret] + self.text[self.caret + 1:]
            self._edited()

    def move(self, delta: int) -> None:
        self.caret = max(0, min(len(self.text), self.caret + int(delta)))
        self._completion = None

    def home(self) -> None:
        self.caret = 0
        self._completion = None

    def end(self) -> None:
        self.caret = len(self.text)
        self._completion = None

    def _word_boundary(self, direction: int) -> int:
        text, i = self.text, self.caret
        if direction < 0:
            while i > 0 and not _is_word_char(text[i - 1]):
                i -= 1
            while i > 0 and _is_word_char(text[i - 1]):
                i -= 1
        else:
            while i < len(text) and not _is_word_char(text[i]):
                i += 1
            while i < len(text) and _is_word_char(text[i]):
                i += 1
        return i

    def move_word(self, direction: int) -> None:
        self.caret = self._word_boundary(-1 if direction < 0 else 1)
        self._completion = None

    def delete_word_back(self) -> None:
        start = self._word_boundary(-1)
        if start < self.caret:
            self.text = self.text[:start] + self.text[self.caret:]
            self.caret = start
            self._edited()

    def _set_text(self, text: str) -> None:
        self.text = text[:self.max_length]
        self.caret = len(self.text)
        self._completion = None

    def history_prev(self) -> None:
        if not self.history:
            return
        if self._history_index is None:
            self._draft = self.text
            self._history_index = len(self.history) - 1
        elif self._history_index > 0:
            self._history_index -= 1
        self._set_text(self.history[self._history_index])

    def history_next(self) -> None:
        if self._history_index is None:
            return
        if self._history_index < len(self.history) - 1:
            self._history_index += 1
            self._set_text(self.history[self._history_index])
        else:
            self._history_index = None
            self._set_text(self._draft)

    def complete(self, names, reverse: bool = False) -> bool:
        state = self._completion
        if state is not None:
            start, matches, index, shown = state
            if self.caret != start + len(shown) or self.text[start:self.caret] != shown:
                state = None
        if state is None:
            start = self.caret
            while start > 0 and not self.text[start - 1].isspace():
                start -= 1
            prefix = self.text[start:self.caret]
            folded = prefix.casefold()
            matches = sorted(
                {str(n) for n in names if str(n).casefold().startswith(folded)},
                key=str.casefold,
            )
            if not matches:
                return False
            index = len(matches) - 1 if reverse else 0
        else:
            index = (index + (-1 if reverse else 1)) % len(matches)
        choice = matches[index]
        old_end = self.caret
        new_text = self.text[:start] + choice + self.text[old_end:]
        if len(new_text) > self.max_length:
            return False
        self.text = new_text
        self.caret = start + len(choice)
        self._completion = (start, matches, index, choice)
        self._history_index = None
        return True

    def submit(self) -> str:
        message = self.text.strip()
        if message and (not self.history or self.history[-1] != message):
            self.history.append(message)
            del self.history[:-CHAT_HISTORY_LIMIT]
        return message
