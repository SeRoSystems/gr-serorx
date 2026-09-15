from gnuradio import gr_unittest

from gnuradio.serorx.adsb import message

KLM1023 = "8D4840D6202CC371C32CE0576098"
POSITION_EVEN = "8D40621D58C382D690C8AC2863A7"
POSITION_ODD = "8D40621D58C386435CC412692AD6"
VELOCITY_GS = "8D485020994409940838175B284F"
VELOCITY_TAS = "8DA05F219B06B6AF189400CBC33F"


class qa_adsb(gr_unittest.TestCase):
    def test_header(self):
        self.assertEqual((message.df(KLM1023), message.icao(KLM1023), message.typecode(KLM1023)), (17, "4840D6", 4))
        self.assertEqual(len(message.bits(KLM1023)), 112)

    def test_callsign(self):
        self.assertEqual(message.callsign(KLM1023), "KLM1023")

    def test_altitude(self):
        self.assertEqual(message.altitude(POSITION_EVEN), 38000)
        self.assertEqual(message.altitude(POSITION_ODD), 38000)
        self.assertIsNone(message.altitude(KLM1023))

    def test_gillham(self):
        # C1 A1 C2 A2 C4 A4 B1 D1 B2 D2 B4 D4
        self.assertEqual(message.gillham("001000000000"), -1000)
        self.assertEqual(message.gillham("000010001010"), -200)
        self.assertEqual(message.gillham("001000101000"), 1000)
        self.assertIsNone(message.gillham("000000000000"))

    def test_nl(self):
        self.assertEqual(message.nl(0), 59)
        self.assertEqual(message.nl(52.2572), 36)
        self.assertEqual(message.nl(87), 2)
        self.assertEqual(message.nl(89), 1)

    def test_global_position(self):
        lat, lon = message.position(POSITION_EVEN, POSITION_ODD, even_newer=True)
        self.assertAlmostEqual(lat, 52.2572, places=4)
        self.assertAlmostEqual(lon, 3.91937, places=4)
        lat, lon = message.position(POSITION_EVEN, POSITION_ODD, even_newer=False)
        self.assertAlmostEqual(lat, 52.26578, places=4)
        self.assertAlmostEqual(lon, 3.92, delta=0.05)

    def test_velocity(self):
        gs = message.velocity(VELOCITY_GS)
        self.assertEqual(gs["speed"], 159)
        self.assertAlmostEqual(gs["track"], 182.88, places=2)
        self.assertEqual(gs["vertical_rate"], -832)
        tas = message.velocity(VELOCITY_TAS)
        self.assertEqual((tas["airspeed"], tas["airspeed_type"]), (375, "TAS"))
        self.assertAlmostEqual(tas["heading"], 243.98, places=2)
        self.assertEqual(tas["vertical_rate"], -2304)

    def test_decoder_pairs_positions(self):
        decoder = message.Decoder()
        self.assertEqual(decoder.decode(KLM1023, 0.0), {"callsign": "KLM1023"})
        self.assertEqual(decoder.decode(POSITION_ODD, 1.0), {"altitude": 38000})
        fields = decoder.decode(POSITION_EVEN, 2.0)
        self.assertEqual(fields["altitude"], 38000)
        self.assertAlmostEqual(fields["latitude"], 52.2572, places=4)
        self.assertAlmostEqual(fields["longitude"], 3.91937, places=4)
        self.assertEqual(decoder.decode(POSITION_ODD, 20.0), {"altitude": 38000})
        self.assertIn("speed", decoder.decode(VELOCITY_GS, 21.0))


class qa_short_reply_fields(gr_unittest.TestCase):
    """The 13 bit field is C1 A1 C2 A2 C4 A4 M/X B1 D1/Q B2 D2 B4 D4, at bits 20 to 32."""

    # Q set, M clear. The 11 bits left after removing M and Q are 1040, so 1040 * 25 - 1000.
    ALTITUDE_FIELD = "1000000110000"
    ALTITUDE_FRAME = "20001030000000"          # DF4 carrying the field above
    # A1 A2 A4 give 7, B2 B4 give 6, no C bit gives 0, D1 gives 1.
    SQUAWK_FIELD = "0101010011010"
    SQUAWK_FRAME = "28000A9A000000"            # DF5 carrying the field above

    def test_ac13_q_bit_path(self):
        self.assertEqual(message.ac13_from_field(self.ALTITUDE_FIELD), 25000)

    def test_ac13_metric_is_none(self):
        metric = self.ALTITUDE_FIELD[:6] + "1" + self.ALTITUDE_FIELD[7:]
        self.assertIsNone(message.ac13_from_field(metric))

    def test_ac13_gillham_path_strips_the_m_bit(self):
        # Q clear leaves a 12 bit Gillham code once M is removed. Removing M is the only work
        # this branch does, so the result must equal the existing decoder on those 12 bits.
        # Both codes below keep M and Q clear. The altitude itself is whatever the Gillham
        # decoder reads, which this test does not restate.
        field = "001010" + "0" + "100100"
        self.assertEqual(message.ac13_from_field(field), message.gillham("001010" + "100100"))

    def test_ac13_invalid_gillham_is_none(self):
        # C1 C2 C4 all clear gives a hundreds digit of 0, which no valid code uses.
        self.assertIsNone(message.ac13_from_field("000000" + "0" + "100100"))

    def test_squawk(self):
        self.assertEqual(message.squawk_from_field(self.SQUAWK_FIELD), "7601")

    def test_squawk_zero_is_none(self):
        self.assertIsNone(message.squawk_from_field("0" * 13))

    def test_altitude_from_a_whole_frame(self):
        self.assertEqual(message.df(self.ALTITUDE_FRAME), 4)
        self.assertEqual(message.ac13_altitude(self.ALTITUDE_FRAME), 25000)

    def test_squawk_from_a_whole_frame(self):
        self.assertEqual(message.df(self.SQUAWK_FRAME), 5)
        self.assertEqual(message.id13_squawk(self.SQUAWK_FRAME), "7601")

    def test_capability(self):
        frame = "5D4840D6000000"
        self.assertEqual(message.df(frame), 11)
        self.assertEqual(message.capability(frame), 5)


if __name__ == "__main__":
    gr_unittest.run(qa_adsb)
