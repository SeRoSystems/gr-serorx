"""ADS-B fields: frame PDU or hex text to a text line and a field dict."""
import datetime
import string

import numpy as np
import pmt
from gnuradio import gr

from . import message
from .modes_slicer import meta_value, stream_clock

HEX_DIGITS = frozenset(string.hexdigits)


class adsb_fields(gr.basic_block):
    """Decodes every DF17 or DF18 frame from `frames`: a PDU with the 14 bytes, a PDU with 28 hex characters,
    or a hex string.

    `lines` carries one text line per frame as a byte PDU: local time, DF, ICAO, type code and the fields.
    `fields` carries a dict with df, icao, tc, frame (the 14 bytes), time (when the frame had one) and the
    decoded fields. Positions need an even and an odd message of the aircraft within 10 s on the stream
    clock (offset / samp_rate from the preamble stage, else the process clock).
    Any other message is dropped with one warning.
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
        self.logger.warn(text)

    def _drop(self):
        if not self._warned:
            self._warned = True
            self._warn("frames expects byte PDUs from the Mode S Frame Check or hex strings, message dropped")

    def _handle(self, msg):
        meta = pmt.PMT_NIL
        if pmt.is_pair(msg) and pmt.is_u8vector(pmt.cdr(msg)):
            meta = pmt.car(msg)
            data = bytes(pmt.u8vector_elements(pmt.cdr(msg)))
            text = data.hex().upper() if len(data) == 14 else data.decode("ascii", "replace").strip().upper()
        elif pmt.is_symbol(msg):
            text = pmt.symbol_to_string(msg).strip().upper()
        else:
            return self._drop()
        if len(text) != 28 or not HEX_DIGITS.issuperset(text):
            return self._drop()
        df = message.df(text)
        if df not in (17, 18):
            return
        fields = self._decoder.decode(text, stream_clock(meta))
        self._count += 1
        clock = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"{clock} DF{df} ICAO={message.icao(text)} TC={message.typecode(text)} " + " ".join(
            f"{key}={value}" for key, value in fields.items())
        line = line.rstrip()
        if self._print:
            print(line, flush=True)
        # Text travels as bytes: a PMT symbol is interned for the life of the process.
        self.message_port_pub(self._lines, pmt.cons(meta, pmt.init_u8vector(len(line), list(line.encode()))))
        record = {"df": df, "icao": message.icao(text), "tc": message.typecode(text),
                  "frame": np.frombuffer(bytes.fromhex(text), dtype=np.uint8)}
        stamp = meta_value(meta, "time")
        if stamp is not None:
            record["time"] = stamp
        record.update(fields)
        self.message_port_pub(self._fields, pmt.to_pmt(record))
