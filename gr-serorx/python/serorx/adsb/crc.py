"""Mode S CRC-24, syndrome tables and repair of one or two flipped bits.

The approach and the constants come from readsb (https://github.com/wiedehopf/readsb), file
crc.c, GPL-3.0, copyright Michael Wolf and Oliver Jowett, itself a fork of dump1090-fa.
The generator polynomial and the field layout are defined in ICAO Annex 10 Volume IV.

The checksum runs over the whole frame including the parity field. A clean DF11, DF17 or DF18
frame gives 0. A frame whose parity is address overlaid gives the sender's address.

The code is linear, so the syndrome of several flipped bits is the exclusive or of their
individual syndromes. The tables invert that for one and two bits. Every syndrome reachable by
one or two flips is distinct, which qa_crc checks. A frame carrying three or more flipped bits
can land on a syndrome the table resolves and is then repaired into a wrong frame. readsb guards
that by enumerating three and four bit patterns, 6.2 million entries for a long frame, which is
out of reach in Python. Depth 1 is the default for that reason.
"""

POLY = 0xFFF409
LONG_BITS = 112
SHORT_BITS = 56
MAX_DEPTH = 2


def _build_table():
    out = []
    for index in range(256):
        value = index << 16
        for _ in range(8):
            value = ((value << 1) ^ POLY) if value & 0x800000 else (value << 1)
        out.append(value & 0xFFFFFF)
    return tuple(out)


TABLE = _build_table()


def checksum(data, bits):
    """The syndrome of a frame: the remainder over its data bytes against its parity field.

    0 for a clean DF11, DF17 or DF18 frame. The sender's address for an address overlaid one.
    The last three bytes are the parity field and enter by exclusive or, not through the table.
    """
    n = bits // 8
    remainder = 0
    for byte in data[:n - 3]:
        remainder = ((remainder << 8) & 0xFFFFFF) ^ TABLE[byte ^ ((remainder >> 16) & 0xFF)]
    return remainder ^ int.from_bytes(data[n - 3:n], "big")


def bit_syndrome(bit, bits):
    """The syndrome of a frame that carries one flipped bit at `bit` and is otherwise zero."""
    frame = bytearray(bits // 8)
    frame[bit // 8] ^= 0x80 >> (bit % 8)
    return checksum(frame, bits)


class ErrorInfo:
    """The bit positions a syndrome resolves to."""

    __slots__ = ("positions", "errors")

    def __init__(self, positions):
        self.positions = positions
        self.errors = len(positions)


_NO_ERRORS = ErrorInfo(())
_TABLES = {}


def _syndromes(bits):
    """The syndrome to bit positions map for a frame length, built on first use."""
    table = _TABLES.get(bits)
    if table is None:
        single = [bit_syndrome(bit, bits) for bit in range(bits)]
        table = {}
        for bit in range(bits):
            table[single[bit]] = (bit,)
        for first in range(bits):
            for second in range(first + 1, bits):
                table.setdefault(single[first] ^ single[second], (first, second))
        _TABLES[bits] = table
    return table


def diagnose(syndrome, bits, depth=1):
    """The error positions behind a syndrome, or None when the tables do not resolve it."""
    if syndrome == 0:
        return _NO_ERRORS
    if depth < 1:
        return None
    positions = _syndromes(bits).get(syndrome)
    if positions is None or len(positions) > depth:
        return None
    return ErrorInfo(positions)


def fix(data, info):
    """The frame with the diagnosed bits flipped back."""
    if not info.positions:
        return bytes(data)
    out = bytearray(data)
    for bit in info.positions:
        out[bit // 8] ^= 0x80 >> (bit % 8)
    return bytes(out)


def correct_address(addr, info):
    """The 24 bit address with the diagnosed flips inside bits 9 to 32 applied."""
    for bit in info.positions:
        if 8 <= bit < 32:
            addr ^= 1 << (31 - bit)
    return addr
