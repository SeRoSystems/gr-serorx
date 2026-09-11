"""ADS-B message fields for DF17 and DF18. Pure Python, no dependencies.

Fields: callsign (TC 1 to 4), altitude (TC 9 to 18 barometric with Q-bit and Gillham codes, TC 20 to 22
GNSS height), position from an even and odd airborne message pair within PAIR_SECONDS (global CPR),
velocity (TC 19: ground speed and track, or airspeed and heading, plus vertical rate). Surface
position messages (TC 5 to 8) carry no position here.
"""
import math

NZ = 15
PAIR_SECONDS = 10.0
FORGET_SECONDS = 60.0
CPR_SCALE = 2 ** 17
CALLSIGN_CHARS = "#ABCDEFGHIJKLMNOPQRSTUVWXYZ##### ###############0123456789######"


def bits(msg):
    """The message as a string of bits, 112 for a long frame."""
    return bin(int(msg, 16))[2:].zfill(len(msg) * 4)


def df(msg):
    return int(msg[:2], 16) >> 3


def icao(msg):
    return msg[2:8].upper()


def typecode(msg):
    return int(msg[8:10], 16) >> 3


def _me(msg):
    return bits(msg)[32:88]


def callsign(msg):
    """Callsign of an identification message, trailing spaces removed."""
    me = _me(msg)
    chars = "".join(CALLSIGN_CHARS[int(me[8 + 6 * i:14 + 6 * i], 2)] for i in range(8))
    return chars.replace("#", "").strip()


def _gray_to_int(code):
    value = 0
    for bit in code:
        value = (value << 1) | ((value & 1) ^ int(bit))
    return value


def gillham(field):
    """Altitude in feet from the 12-bit field C1 A1 C2 A2 C4 A4 B1 D1 B2 D2 B4 D4. None for an invalid code."""
    f = field
    n500 = _gray_to_int(f[7] + f[9] + f[11] + f[1] + f[3] + f[5] + f[6] + f[8] + f[10])
    n100 = _gray_to_int(f[0] + f[2] + f[4])
    if n100 in (0, 5, 6):
        return None
    if n100 == 7:
        n100 = 5
    if n500 % 2:
        n100 = 6 - n100
    return n500 * 500 + n100 * 100 - 1300


def altitude(msg):
    """Altitude in feet: barometric for TC 9 to 18, GNSS height (meters in the message) for TC 20 to 22."""
    tc = typecode(msg)
    field = _me(msg)[8:20]
    if int(field, 2) == 0:
        return None
    if 9 <= tc <= 18:
        if field[7] == "1":
            return int(field[:7] + field[8:], 2) * 25 - 1000
        return gillham(field)
    if 20 <= tc <= 22:
        return int(round(int(field, 2) * 3.28084))
    return None


def nl(lat):
    """Number of longitude zones at a latitude."""
    if lat == 0:
        return 59
    if abs(lat) == 87:
        return 2
    if abs(lat) > 87:
        return 1
    a = 1 - (1 - math.cos(math.pi / (2 * NZ))) / math.cos(math.pi / 180 * lat) ** 2
    return int(math.floor(2 * math.pi / math.acos(a)))


def cpr(msg):
    """(odd flag, latitude, longitude) of an airborne position message, the CPR values scaled to 0 to 1."""
    me = _me(msg)
    return me[21] == "1", int(me[22:39], 2) / CPR_SCALE, int(me[39:56], 2) / CPR_SCALE


def position(even, odd, even_newer):
    """Global CPR: (latitude, longitude) from an even and an odd message. None when they do not pair."""
    _, lat_e, lon_e = cpr(even)
    _, lat_o, lon_o = cpr(odd)
    j = math.floor(59 * lat_e - 60 * lat_o + 0.5)
    lat_even = 360.0 / 60 * ((j % 60) + lat_e)
    lat_odd = 360.0 / 59 * ((j % 59) + lat_o)
    if lat_even >= 270:
        lat_even -= 360
    if lat_odd >= 270:
        lat_odd -= 360
    if nl(lat_even) != nl(lat_odd):
        return None
    lat = lat_even if even_newer else lat_odd
    zones = nl(lat)
    ni = max(zones - (0 if even_newer else 1), 1)
    m = math.floor(lon_e * (zones - 1) - lon_o * zones + 0.5)
    lon = 360.0 / ni * ((m % ni) + (lon_e if even_newer else lon_o))
    if lon > 180:
        lon -= 360
    return round(lat, 5), round(lon, 5)


def velocity(msg):
    """Fields of an airborne velocity message: speed and track (subtypes 1 and 2), airspeed, airspeed_type
    and heading (subtypes 3 and 4), vertical_rate in ft/min."""
    me = _me(msg)
    subtype = int(me[5:8], 2)
    out = {}
    if subtype in (1, 2):
        v_ew, v_ns = int(me[14:24], 2), int(me[25:35], 2)
        if v_ew and v_ns:
            scale = 4 if subtype == 2 else 1
            ew = (v_ew - 1) * scale * (-1 if me[13] == "1" else 1)
            ns = (v_ns - 1) * scale * (-1 if me[24] == "1" else 1)
            out["speed"] = int(round(math.hypot(ew, ns)))
            out["track"] = round(math.degrees(math.atan2(ew, ns)) % 360, 2)
    elif subtype in (3, 4):
        if me[13] == "1":
            out["heading"] = round(int(me[14:24], 2) / 1024 * 360, 2)
        airspeed = int(me[25:35], 2)
        if airspeed:
            out["airspeed"] = (airspeed - 1) * (4 if subtype == 4 else 1)
            out["airspeed_type"] = "TAS" if me[24] == "1" else "IAS"
    rate = int(me[37:46], 2)
    if rate:
        out["vertical_rate"] = (rate - 1) * 64 * (-1 if me[36] == "1" else 1)
    return out


class Decoder:
    """Decodes messages one by one and keeps the last even and odd position message per aircraft."""

    def __init__(self, pair_seconds=PAIR_SECONDS):
        self._pair_seconds = pair_seconds
        self._positions = {}
        self._calls = 0

    def decode(self, msg, timestamp):
        """Fields of one DF17 or DF18 message as a dict. Position fields appear once both CPR halves are known."""
        tc = typecode(msg)
        fields = {}
        if 1 <= tc <= 4:
            fields["callsign"] = callsign(msg)
        elif 9 <= tc <= 18 or 20 <= tc <= 22:
            alt = altitude(msg)
            if alt is not None:
                fields["altitude"] = alt
            fix = self._position(icao(msg), msg, timestamp)
            if fix is not None:
                fields["latitude"], fields["longitude"] = fix
        elif tc == 19:
            fields.update(velocity(msg))
        return fields

    def _position(self, address, msg, timestamp):
        self._calls += 1
        if self._calls % 500 == 0:
            self._forget(timestamp)
        odd, _, _ = cpr(msg)
        pair = self._positions.setdefault(address, {})
        pair[odd] = (msg, timestamp)
        other = pair.get(not odd)
        if other is None or timestamp - other[1] > self._pair_seconds:
            return None
        even, odd_msg = (other[0], msg) if odd else (msg, other[0])
        return position(even, odd_msg, even_newer=not odd)

    def _forget(self, now):
        stale = [address for address, pair in self._positions.items()
                 if all(now - stamp > FORGET_SECONDS for _, stamp in pair.values())]
        for address in stale:
            del self._positions[address]
