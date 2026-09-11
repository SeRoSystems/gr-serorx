import numpy as np
from gnuradio import gr_unittest

from gnuradio.serorx.adsb import modes

KLM1023 = "8D4840D6202CC371C32CE0576098"
POSITION = "8D40621D58C382D690C8AC2863A7"
SPU = 12


def noisy(frames, length=100_000, seed=1):
    rng = np.random.default_rng(seed)
    signal = np.abs(rng.normal(0, 0.02, length) + 1j * rng.normal(0, 0.02, length)).astype(np.float32)
    for start, msg in frames:
        frame = modes.synthesize(msg, SPU, 0.3)
        signal[start:start + len(frame)] += frame
    return signal


class qa_modes(gr_unittest.TestCase):
    def test_samples_per_us(self):
        self.assertEqual(modes.samples_per_us(12e6), 12)
        self.assertEqual(modes.frame_length(12), 1440)
        for rate in (3e6, 5e6, 12.5e6):
            with self.assertRaises(ValueError):
                modes.samples_per_us(rate)

    def test_candidates_and_slice(self):
        signal = noisy([(5_000, KLM1023), (40_000, POSITION), (70_003, KLM1023)])
        found = list(modes.candidates(signal, SPU))
        self.assertTrue({5_000, 40_000, 70_003} <= set(found), found)
        self.assertLessEqual(len(found), 6, found)
        frame, confidence = modes.slice_frame(signal[5_000:5_000 + 1440], SPU)
        self.assertEqual(frame.hex().upper(), KLM1023)
        self.assertEqual(len(confidence), 112)
        self.assertGreater(float(confidence.min()), 0.5)
        self.assertTrue(modes.check(frame))

    def test_noise_only(self):
        self.assertEqual(len(modes.candidates(noisy([], seed=2), SPU)), 0)

    def test_crc_and_df(self):
        data = bytes.fromhex(KLM1023)
        self.assertEqual(modes.crc24(data), 0)
        self.assertEqual(modes.df(data), 17)
        self.assertNotEqual(modes.crc24(bytes.fromhex(KLM1023[:-1] + "9")), 0)
        self.assertFalse(modes.check(bytes.fromhex("5D" + KLM1023[2:])))

    def test_correct_one_weak_bit(self):
        window = modes.synthesize(KLM1023, SPU, 0.3)
        bit = 42
        start = (modes.PREAMBLE_US + bit) * SPU
        # both halves nearly equal, the wrong one slightly stronger: a wrong bit with low confidence
        window[start:start + SPU // 2] = 0.14
        window[start + SPU // 2:start + SPU] = 0.15
        frame, confidence = modes.slice_frame(window, SPU)
        self.assertNotEqual(modes.crc24(frame), 0)
        self.assertEqual(int(np.argmin(confidence)), bit)
        fixed = modes.correct(frame, confidence)
        self.assertEqual(fixed.hex().upper(), KLM1023)
        self.assertIsNone(modes.correct(bytes.fromhex(KLM1023[:-2] + "00"), np.ones(112, dtype=np.float32)))


if __name__ == "__main__":
    gr_unittest.run(qa_modes)
