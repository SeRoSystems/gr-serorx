import grpc
from gnuradio import gr_unittest

import fake_grx
from gnuradio.serorx import grx_client


class qa_grx_client(gr_unittest.TestCase):
    def setUp(self):
        self.fake = fake_grx.FakeGrx(realtime=False).start()
        self.radio = grx_client.radio_id("tunable")
        self.control = grx_client.GrxControl("127.0.0.1", self.fake.control_port, timeout=2.0)
        self.stream = grx_client.GrxStream("127.0.0.1", self.fake.stream_port, timeout=2.0)

    def tearDown(self):
        self.control.close()
        self.stream.close()
        self.fake.stop()

    def test_system_info(self):
        monitor = grx_client.GrxMonitor("127.0.0.1", self.fake.monitor_port, timeout=2.0)
        info = monitor.system_info()
        monitor.close()
        self.assertEqual((info.model, info.hardware, info.image, info.serial),
                         (fake_grx.MODEL, fake_grx.HARDWARE, fake_grx.IMAGE, fake_grx.SERIAL))
        self.assertEqual(info.describe(), f"{fake_grx.MODEL}, serial {fake_grx.SERIAL}, hardware {fake_grx.HARDWARE}, "
                                          f"image {fake_grx.IMAGE}")
        self.assertEqual(grx_client.SystemInfo("", "", "", "", {}).describe(), "model unknown, serial unknown")

    def test_stream_channels(self):
        self.assertEqual(self.stream.channels(), [("1030", 0), ("1090", 0), ("tunable", 0)])
        dead = grx_client.GrxStream("127.0.0.1", 1, timeout=0.5)
        with self.assertRaises(grx_client.GrxError):
            dead.channels()
        dead.close()

    def test_channel_label(self):
        self.assertEqual(grx_client.channel_label("1090", 0), "1090 (index 0)")

    def test_radio_id(self):
        self.assertEqual(grx_client.radio_id("1090", 0).band, grx_client.BANDS["1090"])
        with self.assertRaises(ValueError):
            grx_client.radio_id("2400")

    def test_channels_and_ports(self):
        self.assertEqual(self.control.channels(), [("tunable", 0)])
        self.assertEqual(self.control.rx_ports(self.radio), fake_grx.RX_PORTS)

    def test_set_get(self):
        self.control.set_rx_port(self.radio, 1)
        self.control.set_center_freq(self.radio, 800e6)
        self.control.set_bandwidth(self.radio, 5e6)
        self.control.set_gain(self.radio, 12)
        self.control.set_samp_rate(self.radio, 6e6)
        self.assertEqual(self.control.get_rx_port(self.radio), 1)
        self.assertEqual(self.control.get_center_freq(self.radio), 800_000_000)
        self.assertEqual(self.control.get_bandwidth(self.radio), 5_000_000)
        self.assertEqual(self.control.get_gain(self.radio), 12)
        self.assertEqual(self.control.get_samp_rate(self.radio), 6_000_000)

    def test_invalid_argument(self):
        with self.assertRaises(grx_client.GrxError) as ctx:
            self.control.set_gain(self.radio, 99)
        self.assertEqual(ctx.exception.code, grpc.StatusCode.INVALID_ARGUMENT)
        self.assertIn("hardware_gain_db", str(ctx.exception))
        self.assertIn(self.control.endpoint, str(ctx.exception))

    def test_unreachable(self):
        dead = grx_client.GrxControl("127.0.0.1", 1, timeout=0.5)
        with self.assertRaises(grx_client.GrxError) as ctx:
            dead.channels()
        self.assertIn(ctx.exception.code, (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED))
        self.assertIn("127.0.0.1:1", str(ctx.exception))
        self.assertEqual(grx_client.reachability(ctx.exception), "refused")
        dead.close()

    def test_reachability(self):
        """UNAVAILABLE is classified by a TCP probe of the endpoint, not by the wording of the gRPC details."""
        unavailable = grpc.StatusCode.UNAVAILABLE

        def err(endpoint, code=unavailable):
            return grx_client.GrxError(endpoint, code, "Verbindung verweigert")

        self.assertEqual(grx_client.reachability(err("h:1", grpc.StatusCode.DEADLINE_EXCEEDED)), "timeout")
        self.assertEqual(grx_client.reachability(err("127.0.0.1:1")), "refused")
        self.assertEqual(grx_client.reachability(err("grx.invalid:5309")), "unresolved")
        self.assertEqual(grx_client.reachability(err("192.0.2.1:5309"), timeout=0.3), "timeout")
        self.assertEqual(grx_client.reachability(err(self.control.endpoint)), "failed")
        self.assertEqual(grx_client.reachability(err("no port")), "failed")
        self.assertIsNone(grx_client.reachability(err("h:1", grpc.StatusCode.INVALID_ARGUMENT)))

    def test_properties_and_stream(self):
        props = self.stream.properties(self.radio)
        self.assertEqual(props, grx_client.StreamProperties(1_090_000_000, 12_000_000, fake_grx.CALIBRATION_DB))
        blocks = list(self.stream.start(self.radio, requested_blocks=2))
        self.assertEqual(len(blocks), 2)
        self.assertEqual(len(blocks[0].samples), fake_grx.BLOCK_SAMPLES * 4)
        self.assertEqual(blocks[0].lost_blocks, 0)
        self.assertLess(blocks[0].timestamp, blocks[1].timestamp)

    def test_cancel(self):
        it = self.stream.start(self.radio)
        next(it)
        it.cancel()
        with self.assertRaises(grx_client.GrxError) as ctx:
            next(it)
        self.assertEqual(ctx.exception.code, grpc.StatusCode.CANCELLED)


if __name__ == "__main__":
    gr_unittest.run(qa_grx_client)
