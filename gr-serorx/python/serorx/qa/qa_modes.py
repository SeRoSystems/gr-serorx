import numpy as np
from gnuradio import gr_unittest

from gnuradio.serorx.adsb import modes

KLM1023 = "8D4840D6202CC371C32CE0576098"
POSITION = "8D40621D58C382D690C8AC2863A7"
SHORT = "02E19838CAF877"
SPU = 12


def noise(length, seed, sigma=0.02):
    rng = np.random.default_rng(seed)
    return np.abs(rng.normal(0, sigma, length) + 1j * rng.normal(0, sigma, length)).astype(np.float32)


def noisy(frames, length=100_000, seed=1, amplitude=0.3):
    signal = noise(length, seed)
    for start, msg in frames:
        frame = modes.synthesize(msg, SPU, amplitude)
        signal[start:start + len(frame)] += frame
    return signal


class qa_modes(gr_unittest.TestCase):
    def test_samples_per_us(self):
        self.assertEqual(modes.samples_per_us(12e6), 12)
        self.assertEqual(modes.frame_length(12), 1440)
        self.assertEqual(modes.frame_length(12, 56), 768)
        for rate in (3e6, 5e6, 12.5e6):
            with self.assertRaises(ValueError):
                modes.samples_per_us(rate)

    def test_alignments(self):
        self.assertEqual(modes.alignments(5), (-2, -1, 0, 1, 2))
        self.assertEqual(modes.alignments(3), (-1, 0, 1))
        self.assertEqual(modes.alignments(1), (0,))

    def test_round_trip(self):
        signal = modes.synthesize(KLM1023, SPU)
        data, confidence = modes.slice_frame(signal, 0, SPU)
        self.assertEqual(data.hex().upper(), KLM1023)
        self.assertGreater(confidence.min(), 0.9)

    def test_short_frame_round_trip(self):
        signal = modes.synthesize(SHORT, SPU)
        self.assertEqual(len(signal), modes.frame_length(SPU, 56))
        data, _ = modes.slice_frame(signal, 0, SPU, 56)
        self.assertEqual(len(data), 7)
        self.assertEqual(data.hex().upper(), SHORT)

    def test_candidate_found_at_the_frame_start(self):
        found = modes.candidates(noisy([(5000, KLM1023)]), SPU)
        self.assertTrue(any(abs(int(index) - 5000) <= 2 for index in found))

    def test_weak_frame_beside_a_strong_one(self):
        # A block median as the noise reference is pulled up by the strong frame and loses the
        # weak one. A reference taken from the gaps inside each candidate is not.
        signal = noise(100_000, 7)
        signal[5000:5000 + modes.frame_length(SPU)] += modes.synthesize(KLM1023, SPU, 3.0)
        signal[40_000:40_000 + modes.frame_length(SPU)] += modes.synthesize(POSITION, SPU, 0.12)
        found = [int(index) for index in modes.candidates(signal, SPU)]
        self.assertTrue(any(abs(index - 5000) <= 2 for index in found), "strong frame missing")
        self.assertTrue(any(abs(index - 40_000) <= 2 for index in found), "weak frame missing")

    def test_noise_alone_gives_few_candidates(self):
        self.assertLess(len(modes.candidates(noise(200_000, 3), SPU)), 200)

    def test_removed_helpers_are_gone(self):
        for name in ("crc24", "correct", "check"):
            self.assertFalse(hasattr(modes, name), name)


if __name__ == "__main__":
    gr_unittest.run(qa_modes)
