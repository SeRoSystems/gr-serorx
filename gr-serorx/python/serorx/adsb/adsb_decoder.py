"""ADS-B decoder: Mode S demodulation and field decoding wired into one block."""
from gnuradio import gr

from .adsb_fields import adsb_fields
from .modes_demod import modes_demod


class adsb_decoder(gr.hier_block2):
    """Sink for a complex baseband stream. Prints decoded lines and publishes them on `frames`.

    Inside: modes_demod and adsb_fields. samp_rate must be an even number of MSps. The decoded
    fields also leave as dicts on `fields`.
    """

    def __init__(self, samp_rate=12e6, correct_bits=1, alignments=5, short_replies=True,
                 threshold=3.0, icao_ttl=60.0, print_lines=True):
        gr.hier_block2.__init__(self, "adsb_decoder",
                                gr.io_signature(1, 1, gr.sizeof_gr_complex),
                                gr.io_signature(0, 0, 0))
        self.message_port_register_hier_out("frames")
        self.message_port_register_hier_out("fields")
        self.demod = modes_demod(samp_rate, correct_bits, alignments, short_replies, threshold,
                                 icao_ttl)
        self.fields = adsb_fields(print_lines)
        self.connect(self, self.demod)
        self.msg_connect((self.demod, "frames"), (self.fields, "frames"))
        self.msg_connect((self.fields, "lines"), (self, "frames"))
        self.msg_connect((self.fields, "fields"), (self, "fields"))

    def frame_count(self):
        return self.demod.frame_count()

    def set_samp_rate(self, samp_rate):
        self.demod.set_samp_rate(samp_rate)
