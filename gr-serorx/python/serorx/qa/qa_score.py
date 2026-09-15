from gnuradio import gr_unittest

from gnuradio.serorx.adsb import crc, score

KLM1023 = "8D4840D6202CC371C32CE0576098"
ADDRESS = 0x4840D6


def overlaid(df, addr, payload=b"\x00\x19\x38"):
    """A short reply of `df` from `addr`, parity overlaid, so its checksum is the address."""
    body = bytes([df << 3]) + payload
    return body + (crc.checksum(body + b"\x00" * 3, 56) ^ addr).to_bytes(3, "big")


class qa_score(gr_unittest.TestCase):
    def setUp(self):
        self.icao = score.IcaoFilter()

    def test_message_bits(self):
        self.assertEqual(score.message_bits(17), 112)
        self.assertEqual(score.message_bits(16), 112)
        self.assertEqual(score.message_bits(4), 56)
        self.assertEqual(score.message_bits(11), 56)

    def test_extended_squitter_unknown_then_known(self):
        data = bytes.fromhex(KLM1023)
        result = score.evaluate(data, 112, self.icao)
        self.assertEqual(result.score, 1400)
        self.assertEqual(result.icao, ADDRESS)
        self.assertEqual(result.errors, 0)
        self.icao.add(ADDRESS)
        self.assertEqual(score.evaluate(data, 112, self.icao).score, 1800)

    def test_one_bit_error_halves_the_score(self):
        broken = bytearray(bytes.fromhex(KLM1023))
        broken[6] ^= 0x04
        result = score.evaluate(bytes(broken), 112, self.icao, depth=1)
        self.assertEqual(result.score, 700)
        self.assertEqual(result.errors, 1)
        self.assertEqual(result.data, bytes.fromhex(KLM1023))

    def test_two_bit_error_refused_at_depth_one(self):
        broken = bytearray(bytes.fromhex(KLM1023))
        broken[6] ^= 0x04
        broken[11] ^= 0x40
        self.assertLess(score.evaluate(bytes(broken), 112, self.icao, depth=1).score, 0)
        deeper = score.evaluate(bytes(broken), 112, self.icao, depth=2)
        self.assertGreater(deeper.score, 0)
        self.assertEqual(deeper.data, bytes.fromhex(KLM1023))

    def test_df_field_error_recovered(self):
        broken = bytearray(bytes.fromhex(KLM1023))
        broken[0] ^= 0x40                      # DF 17 becomes DF 25
        self.assertEqual(broken[0] >> 3, 25)
        result = score.evaluate(bytes(broken), 112, self.icao, depth=1)
        self.assertEqual(result.score, 700)
        self.assertEqual(result.df, 17)
        self.assertEqual(result.data, bytes.fromhex(KLM1023))

    def test_df_field_error_not_recovered_without_correction(self):
        broken = bytearray(bytes.fromhex(KLM1023))
        broken[0] ^= 0x40
        self.assertLess(score.evaluate(bytes(broken), 112, self.icao, depth=0).score, 0)

    def test_overlaid_reply_needs_the_filter(self):
        frame = overlaid(4, ADDRESS)
        accepted = score.SHORT_REPLIES
        self.assertLess(score.evaluate(frame, 56, self.icao, accepted=accepted).score, 0)
        self.icao.add(ADDRESS)
        result = score.evaluate(frame, 56, self.icao, accepted=accepted)
        self.assertEqual(result.score, 1000)
        self.assertEqual(result.icao, ADDRESS)
        self.assertEqual(result.df, 4)

    def test_short_reply_rejected_when_short_replies_are_off(self):
        self.icao.add(ADDRESS)
        self.assertLess(score.evaluate(overlaid(4, ADDRESS), 56, self.icao).score, 0)

    def test_all_call_reply(self):
        body = bytes([11 << 3, 0x48, 0x40, 0xD6])
        frame = body + crc.checksum(body + b"\x00" * 3, 56).to_bytes(3, "big")
        self.assertEqual(crc.checksum(frame, 56), 0)
        result = score.evaluate(frame, 56, self.icao, accepted=score.SHORT_REPLIES)
        self.assertEqual(result.score, 750)
        self.assertEqual(result.icao, ADDRESS)
        self.icao.add(ADDRESS)
        self.assertEqual(score.evaluate(frame, 56, self.icao,
                                        accepted=score.SHORT_REPLIES).score, 1600)

    def test_all_zero_frame_rejected(self):
        self.assertLess(score.evaluate(b"\x00" * 14, 112, self.icao,
                                       accepted=score.SHORT_REPLIES).score, 0)

    def test_too_few_bits_rejected(self):
        self.assertLess(score.evaluate(bytes.fromhex(KLM1023), 32, self.icao).score, 0)

    def test_filter_expires_after_two_flips(self):
        self.icao.add(ADDRESS)
        self.icao.expire(0.0)
        self.assertTrue(self.icao.test(ADDRESS))
        self.icao.expire(61.0)
        self.assertTrue(self.icao.test(ADDRESS))
        self.icao.expire(122.0)
        self.assertFalse(self.icao.test(ADDRESS))


if __name__ == "__main__":
    gr_unittest.run(qa_score)
