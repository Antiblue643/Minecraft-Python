from __future__ import annotations

import math


class JavaRandom:
    _MULTIPLIER = 0x5DEECE66D
    _ADDEND = 0xB
    _MASK = (1 << 48) - 1

    def __init__(self, seed: int = 0):
        self.seed = 0
        self._have_next_gaussian = False
        self._next_gaussian = 0.0
        self.set_seed(seed)

    def set_seed(self, seed: int) -> None:
        self.seed = (int(seed) ^ self._MULTIPLIER) & self._MASK
        self._have_next_gaussian = False
        self._next_gaussian = 0.0

    setSeed = set_seed

    def _next(self, bits: int) -> int:
        self.seed = (self.seed * self._MULTIPLIER + self._ADDEND) & self._MASK
        return self.seed >> (48 - bits)

    def next_int(self, bound: int | None = None) -> int:
        if bound is None:
            value = self._next(32)
            return value if value < (1 << 31) else value - (1 << 32)

        bound = int(bound)
        if bound <= 0:
            raise ValueError("bound must be positive")
        if bound & (bound - 1) == 0:
            return (bound * self._next(31)) >> 31
        while True:
            bits = self._next(31)
            value = bits % bound
            if bits - value + (bound - 1) < (1 << 31):
                return value

    nextInt = next_int

    def next_long(self) -> int:
        high = self._next(32)
        low = self._next(32)
        if high >= (1 << 31):
            high -= 1 << 32
        if low >= (1 << 31):
            low -= 1 << 32
        value = (high << 32) + low
        value &= (1 << 64) - 1
        return value if value < (1 << 63) else value - (1 << 64)

    nextLong = next_long

    def next_boolean(self) -> bool:
        return self._next(1) != 0

    nextBoolean = next_boolean

    def next_float(self) -> float:
        return self._next(24) / float(1 << 24)

    nextFloat = next_float

    def next_double(self) -> float:
        return ((self._next(26) << 27) + self._next(27)) / float(1 << 53)

    nextDouble = next_double

    def next_gaussian(self) -> float:
        if self._have_next_gaussian:
            self._have_next_gaussian = False
            return self._next_gaussian
        while True:
            first = 2.0 * self.next_double() - 1.0
            second = 2.0 * self.next_double() - 1.0
            radius_squared = first * first + second * second
            if 0.0 < radius_squared < 1.0:
                break
        multiplier = math.sqrt(-2.0 * math.log(radius_squared) / radius_squared)
        self._next_gaussian = second * multiplier
        self._have_next_gaussian = True
        return first * multiplier

    nextGaussian = next_gaussian

    def random(self) -> float:
        return self.next_double()

    def randrange(self, stop: int) -> int:
        return self.next_int(stop)

    def randint(self, a: int, b: int) -> int:
        if b < a:
            raise ValueError("empty range for randint")
        return a + self.next_int(b - a + 1)
