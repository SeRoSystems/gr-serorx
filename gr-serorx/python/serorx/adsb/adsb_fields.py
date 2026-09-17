"""ADS-B and Mode S fields: a frame PDU to a text line and a field dict."""
import datetime
import sys
import time

import numpy as np
import pmt
from gnuradio import gr

from . import message

EXTENDED = (17, 18)
ALTITUDE_DF = (0, 4, 16, 20)
IDENTITY_DF = (5, 21)
META_KEYS = ("score", "level", "errors", "time")


def meta_value(meta, key):
    value = pmt.dict_ref(meta, pmt.intern(key), pmt.PMT_NIL)
    return None if pmt.is_null(value) else pmt.to_python(value)


def stream_clock(meta):
    """Seconds on the stream clock, from the sample offset where the meta carries one."""
    offset = meta_value(meta, "offset")
    rate = meta_value(meta, "samp_rate")
    if offset is None or not rate:
        return time.monotonic()
    return offset / rate


class adsb_fields(gr.basic_block):
    """Decodes the frames from Mode S Demod.

    `lines` carries one text line per frame as a byte PDU: local time, downlink format, address,
    and the fields. `fields` carries a dict with df, icao, the frame bytes, the score, the signal
    level, the repaired bit count, the time, and the decoded fields.

    DF17 and DF18 give callsign, altitude, position and velocity. Positions need an even and an
    odd message of the aircraft within 10 s on the stream clock. DF0, 4, 16 and 20 give altitude,
    DF5 and DF21 give the squawk, DF11 gives the capability. Any other message is dropped with
    one warning.
    """

    def __init__(self, print_lines=True):
        gr.basic_block.__init__(self, name="adsb_fields", in_sig=None, out_sig=None)
        self._print = bool(print_lines)
        self._decoder = message.Decoder()
        self._in = pmt.intern("frames")
        self._lines = pmt.intern("lines")
        self._fields = pmt.intern("fields")
        self.message_port_register_in(self._in)
        self.message_port_register_out(self._lines)
        self.message_port_register_out(self._fields)
        self.set_msg_handler(self._in, self._handle)
        self._count = 0
        self._warned = False

    def frame_count(self):
        return self._count

    def _warn(self, text):
        # A Python block only has logger in the later 3.10 releases. When unavailable, the line goes to stderr.
        logger = getattr(self, "logger", None)
        if logger is None:
            print(f"adsb_fields :warn: {text}", file=sys.stderr)
            return
        logger.warn(text)

    def _drop(self):
        if not self._warned:
            self._warned = True
            self._warn("frames expects byte PDUs from Mode S Demod, message dropped")

    def _decode(self, df, text, meta):
        if df in EXTENDED:
            return self._decoder.decode(text, stream_clock(meta))
        if df in ALTITUDE_DF:
            altitude = message.ac13_altitude(text)
            return {} if altitude is None else {"alt": altitude}
        if df in IDENTITY_DF:
            squawk = message.id13_squawk(text)
            return {} if squawk is None else {"squawk": squawk}
        if df == 11:
            return {"ca": message.capability(text)}
        return {}

    def _handle(self, msg):
        if not (pmt.is_pair(msg) and pmt.is_u8vector(pmt.cdr(msg))):
            return self._drop()
        data = bytes(pmt.u8vector_elements(pmt.cdr(msg)))
        if len(data) not in (7, 14):
            return self._drop()
        meta = pmt.car(msg)
        text = data.hex().upper()
        df = meta_value(meta, "df")
        if df is None:
            df = data[0] >> 3
        icao = meta_value(meta, "icao")
        icao_text = f"{icao:06X}" if icao is not None else message.icao(text)
        fields = self._decode(df, text, meta)
        self._count += 1
        clock = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        head = f"{clock} DF{df} ICAO={icao_text}"
        if df in EXTENDED:
            head += f" TC={message.typecode(text)}"
        line = (head + " " + " ".join(f"{key}={value}" for key, value in fields.items())).rstrip()
        if self._print:
            print(line, flush=True)
        # Text travels as bytes: a PMT symbol is interned for the life of the process.
        self.message_port_pub(self._lines,
                              pmt.cons(meta, pmt.init_u8vector(len(line), list(line.encode()))))
        record = {"df": df, "icao": icao_text,
                  "frame": np.frombuffer(data, dtype=np.uint8)}
        if df in EXTENDED:
            record["tc"] = message.typecode(text)
        for key in META_KEYS:
            value = meta_value(meta, key)
            if value is not None:
                record[key] = value
        record.update(fields)
        self.message_port_pub(self._fields, pmt.to_pmt(record))
