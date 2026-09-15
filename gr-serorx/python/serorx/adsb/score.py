"""Mode S message scoring and the recently seen address filter.

The scale and the acceptance rules come from readsb (https://github.com/wiedehopf/readsb),
scoreModesMessage in mode_s.c and icao_filter.c, GPL-3.0, copyright Michael Wolf and
Oliver Jowett.

A candidate is rated rather than accepted or refused, so the decoder can slice one preamble at
several alignments and keep the best reading. The scale puts a clean frame from a known aircraft
above a clean frame from an unknown one, and a repaired frame below both.

DF0, 4, 5, 16, 20 and 21 overlay the aircraft address on their parity field, AP in the standard,
so `crc.checksum` returns the address rather than 0. A bit error returns a different address, not
a detectable failure, which leaves the address as the only thing to check. That is what the filter
holds.
"""
from . import crc

LONG_ONLY = frozenset((17, 18))
SHORT_REPLIES = frozenset((0, 4, 5, 11, 16, 17, 18, 20, 21))
OVERLAID = frozenset((0, 4, 5, 16, 20, 21))
DF17_NEIGHBOURS = frozenset((1, 16, 19, 21, 25))
TTL = 60.0


def message_bits(df):
    """112 bits for DF 16 and above, 56 below."""
    return 112 if df >= 16 else 56


def address(data):
    """The AA field, bits 9 to 32."""
    return int.from_bytes(data[1:4], "big")


class IcaoFilter:
    """Addresses seen recently, in two sets swapped every `ttl` seconds.

    An address stays testable for one to two intervals after its last sighting.
    """

    def __init__(self, ttl=TTL):
        self._ttl = float(ttl)
        self._current = set()
        self._previous = set()
        self._next_flip = None

    def add(self, addr):
        self._current.add(addr)

    def test(self, addr):
        return addr in self._current or addr in self._previous

    def expire(self, now):
        if self._next_flip is None:
            self._next_flip = now + self._ttl
        elif now >= self._next_flip:
            self._previous = self._current
            self._current = set()
            self._next_flip = now + self._ttl


class Result:
    """One rated reading of a candidate. A score below zero is a rejection."""

    __slots__ = ("score", "data", "df", "icao", "errors")

    def __init__(self, score, data=b"", df=-1, icao=None, errors=0):
        self.score = score
        self.data = data
        self.df = df
        self.icao = icao
        self.errors = errors


def _repair_df17(data, depth):
    """The frame with its DF field forced to 17, when that gives a valid CRC. None otherwise.

    DF 1, 16, 19, 21 and 25 are each one flipped bit away from 17.
    """
    if depth < 1 or (data[0] >> 3) not in DF17_NEIGHBOURS:
        return None
    patched = bytearray(data)
    patched[0] = (patched[0] & 0x07) | (17 << 3)
    if crc.checksum(patched, 112) == 0:
        return bytes(patched)
    return None


def evaluate(data, bits, icao, depth=1, accepted=LONG_ONLY):
    """Rate one reading of a frame."""
    if bits < 56:
        return Result(-2)

    if bits >= 112 and 17 in accepted:
        patched = _repair_df17(data, depth)
        if patched is not None:
            addr = address(patched)
            return Result(900 if icao.test(addr) else 700, patched, 17, addr, 1)

    df = data[0] >> 3
    if df not in accepted:
        return Result(-2)
    msgbits = message_bits(df)
    if bits < msgbits:
        return Result(-2)
    if not any(data[:7]):
        return Result(-2)

    frame = bytes(data[:msgbits // 8])
    syndrome = crc.checksum(frame, msgbits)

    if df in OVERLAID:
        if not icao.test(syndrome):
            return Result(-1)
        return Result(1000, frame, df, syndrome, 0)

    if df == 11:
        addr = address(frame)
        if syndrome & 0xFFFF80:
            # The lower seven bits carry the interrogator identity, so diagnose the full CRC
            # under the assumption that it is zero. Two bit errors are ambiguous here.
            info = crc.diagnose(syndrome, msgbits, 1)
            if info is None or info.errors > 1:
                return Result(-2)
            addr = crc.correct_address(addr, info)
            if not icao.test(addr):
                return Result(-1)
            return Result(800, crc.fix(frame, info), 11, addr, info.errors)
        if syndrome & 0x7F:
            if not icao.test(addr):
                return Result(-1)
            return Result(1000, frame, 11, addr, 0)
        return Result(1600 if icao.test(addr) else 750, frame, 11, addr, 0)

    info = crc.diagnose(syndrome, msgbits, depth)
    if info is None:
        return Result(-2)
    repaired = crc.fix(frame, info)
    addr = address(repaired)
    base = 1800 if icao.test(addr) else 1400
    return Result(base // (info.errors + 1), repaired, df, addr, info.errors)
