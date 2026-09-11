"""ADS-B decoder: the four Mode S and ADS-B stages wired into one block."""
from gnuradio import gr

from .adsb_fields import adsb_fields
from .modes_frame_check import modes_frame_check
from .modes_preamble import modes_preamble
from .modes_slicer import modes_slicer


class adsb_decoder(gr.hier_block2):
    """Sink for a magnitude stream. Prints decoded ADS-B lines and publishes them as byte PDUs on `frames`.

    Inside: modes_preamble, modes_slicer, modes_frame_check (DF 17 and 18, single-bit correction),
    adsb_fields. samp_rate must be an even number of MSps. threshold is the preamble pulse to gap ratio.
    """

    def __init__(self, samp_rate=12e6, threshold=3.0, print_lines=True):
        gr.hier_block2.__init__(self, "adsb_decoder", gr.io_signature(1, 1, gr.sizeof_float), gr.io_signature(0, 0, 0))
        self.message_port_register_hier_out("frames")
        self.preamble = modes_preamble(samp_rate, threshold)
        self.slicer = modes_slicer()
        self.check = modes_frame_check()
        self.fields = adsb_fields(print_lines)
        self.connect(self, self.preamble)
        self.msg_connect((self.preamble, "windows"), (self.slicer, "windows"))
        self.msg_connect((self.slicer, "bits"), (self.check, "bits"))
        self.msg_connect((self.check, "frames"), (self.fields, "frames"))
        self.msg_connect((self.fields, "lines"), (self, "frames"))

    def frame_count(self):
        return self.check.frame_count()

    def set_samp_rate(self, samp_rate):
        self.preamble.set_samp_rate(samp_rate)
