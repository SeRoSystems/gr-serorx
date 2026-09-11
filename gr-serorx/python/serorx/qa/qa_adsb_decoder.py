import numpy as np
import pmt
from gnuradio import blocks, gr, gr_unittest

from gnuradio.serorx.adsb import adsb_decoder, modes

KLM1023 = "8D4840D6202CC371C32CE0576098"
POSITION = "8D40621D58C382D690C8AC2863A7"
SPU = 12


def ppm(msg, amplitude=0.3):
    """Magnitude samples of one Mode S frame at 12 MSps: preamble and 112 PPM bits."""
    bits = np.unpackbits(np.frombuffer(bytes.fromhex(msg), dtype=np.uint8))
    half = SPU // 2
    out = np.zeros((8 + len(bits)) * SPU, dtype=np.float32)
    for us in (0, 1, 3.5, 4.5):
        start = int(us * SPU)
        out[start:start + half] = amplitude
    for i, bit in enumerate(bits):
        start = (8 + i) * SPU + (0 if bit else half)
        out[start:start + half] = amplitude
    return out


class qa_adsb_decoder(gr_unittest.TestCase):
    def run_signal(self, signal, **kwargs):
        tb = gr.top_block()
        src = blocks.vector_source_f(signal.tolist())
        dec = adsb_decoder(print_lines=False, **kwargs)
        dbg = blocks.message_debug()
        tb.connect(src, dec)
        tb.msg_connect(dec, "frames", dbg, "store")
        tb.run()
        lines = [bytes(pmt.u8vector_elements(pmt.cdr(dbg.get_message(i)))).decode() for i in range(dbg.num_messages())]
        return dec, lines

    def test_frames_in_noise(self):
        rng = np.random.default_rng(1)
        signal = np.abs(rng.normal(0, 0.02, 150_000) + 1j * rng.normal(0, 0.02, 150_000)).astype(np.float32)
        for start, msg in ((5_000, KLM1023), (40_000, POSITION), (100_003, KLM1023)):
            frame = ppm(msg)
            signal[start:start + len(frame)] += frame
        dec, lines = self.run_signal(signal)
        self.assertEqual(dec.frame_count(), 3)
        self.assertEqual(len(lines), 3)
        self.assertIn("ICAO=4840D6", lines[0])
        self.assertIn("ICAO=40621D", lines[1])
        self.assertIn("DF17", lines[2])
        self.assertTrue(lines[0].endswith("DF17 ICAO=4840D6 TC=4 callsign=KLM1023"), lines[0])
        self.assertTrue(lines[1].endswith("DF17 ICAO=40621D TC=11 altitude=38000"), lines[1])

    def test_noise_only(self):
        rng = np.random.default_rng(2)
        signal = np.abs(rng.normal(0, 0.02, 200_000) + 1j * rng.normal(0, 0.02, 200_000)).astype(np.float32)
        dec, lines = self.run_signal(signal)
        self.assertEqual(dec.frame_count(), 0)
        self.assertEqual(lines, [])

    def test_set_samp_rate(self):
        rng = np.random.default_rng(3)
        signal = np.abs(rng.normal(0, 0.02, 50_000) + 1j * rng.normal(0, 0.02, 50_000)).astype(np.float32)
        frame = modes.synthesize(KLM1023, 4, 0.3)
        signal[5_000:5_000 + len(frame)] += frame
        tb = gr.top_block()
        dec = adsb_decoder(print_lines=False)
        dec.set_samp_rate(4e6)
        tb.connect(blocks.vector_source_f(signal.tolist()), dec)
        tb.run()
        self.assertEqual(dec.frame_count(), 1)

    def test_bad_rate(self):
        with self.assertRaises(ValueError):
            adsb_decoder(samp_rate=3e6)


if __name__ == "__main__":
    gr_unittest.run(qa_adsb_decoder)
