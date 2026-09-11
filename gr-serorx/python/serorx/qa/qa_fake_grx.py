import threading
import time

import grpc
import numpy as np
from gnuradio import gr_unittest
from google.protobuf import empty_pb2

import fake_grx
from gnuradio.serorx.proto import (Common_pb2, Monitord_pb2_grpc, Samplestreamingd_pb2, Samplestreamingd_pb2_grpc,
                                   TunableChanneld_pb2, TunableChanneld_pb2_grpc)

TUNABLE = Common_pb2.RadioIdentification(band=Common_pb2.BAND_TUNABLE, per_band_index=0)
FIXED_1090 = Common_pb2.RadioIdentification(band=Common_pb2.BAND_1090_MHZ, per_band_index=0)
PERIOD_NS = fake_grx.BLOCK_SAMPLES * 1_000_000_000 // 12_000_000


class qa_fake_grx(gr_unittest.TestCase):
    def setUp(self):
        self.fake = fake_grx.FakeGrx(realtime=False).start()
        self.control_channel = grpc.insecure_channel(f"127.0.0.1:{self.fake.control_port}")
        self.stream_channel = grpc.insecure_channel(f"127.0.0.1:{self.fake.stream_port}")
        self.control = TunableChanneld_pb2_grpc.TunableChanneldStub(self.control_channel)
        self.stream = Samplestreamingd_pb2_grpc.SamplestreamingdStub(self.stream_channel)

    def tearDown(self):
        self.control_channel.close()
        self.stream_channel.close()
        self.fake.stop()

    def test_channels_and_ports(self):
        reply = self.control.GetChannels(empty_pb2.Empty())
        self.assertEqual([(c.band, c.per_band_index) for c in reply.channels], [(Common_pb2.BAND_TUNABLE, 0)])
        ports = self.control.GetRXPorts(TunableChanneld_pb2.GetRXPortsRequest(channel=TUNABLE)).rx_port_to_label
        self.assertEqual(dict(ports), fake_grx.RX_PORTS)

    def test_monitor(self):
        with grpc.insecure_channel(f"127.0.0.1:{self.fake.monitor_port}") as channel:
            info = Monitord_pb2_grpc.MonitordStub(channel).GetSystemInformation(empty_pb2.Empty())
        self.assertEqual(info.serial_number, fake_grx.SERIAL)
        self.assertEqual(info.version_information["receiver-os.device-type"], fake_grx.MODEL)
        self.assertEqual(info.version_information["receiver-os.image-version"], fake_grx.IMAGE)

    def test_center_frequency_step(self):
        self.control.SetCenterFrequency(TunableChanneld_pb2.SetCenterFrequencyRequest(channel=TUNABLE, center_frequency_hz=900_000_999))
        self.assertEqual(self.control.GetCenterFrequency(TunableChanneld_pb2.GetCenterFrequencyRequest(channel=TUNABLE)).center_frequency_hz, 900_000_000)

    def test_set_get_roundtrip(self):
        self.control.SetRXPort(TunableChanneld_pb2.SetRXPortRequest(channel=TUNABLE, rx_port=1))
        self.control.SetCenterFrequency(TunableChanneld_pb2.SetCenterFrequencyRequest(channel=TUNABLE, center_frequency_hz=900_000_000))
        self.control.SetHardwareGain(TunableChanneld_pb2.SetHardwareGainRequest(channel=TUNABLE, hardware_gain_db=20))
        self.control.SetSampleRate(TunableChanneld_pb2.SetSampleRateRequest(channel=TUNABLE, sample_rate_sps=4_000_000))
        self.control.SetAnalogBandwidth(TunableChanneld_pb2.SetAnalogBandwidthRequest(channel=TUNABLE, analog_bandwidth_hz=3_000_000))
        self.assertEqual(self.control.GetRXPort(TunableChanneld_pb2.GetRXPortRequest(channel=TUNABLE)).rx_port, 1)
        self.assertEqual(self.control.GetCenterFrequency(TunableChanneld_pb2.GetCenterFrequencyRequest(channel=TUNABLE)).center_frequency_hz, 900_000_000)
        self.assertEqual(self.control.GetHardwareGain(TunableChanneld_pb2.GetHardwareGainRequest(channel=TUNABLE)).hardware_gain_db, 20)
        self.assertEqual(self.control.GetSampleRate(TunableChanneld_pb2.GetSampleRateRequest(channel=TUNABLE)).sample_rate_sps, 4_000_000)
        self.assertEqual(self.control.GetAnalogBandwidth(TunableChanneld_pb2.GetAnalogBandwidthRequest(channel=TUNABLE)).analog_bandwidth_hz, 3_000_000)
        self.assertEqual(self.fake.settings["gain"], 20)

    def test_invalid_argument(self):
        with self.assertRaises(grpc.RpcError) as ctx:
            self.control.SetCenterFrequency(TunableChanneld_pb2.SetCenterFrequencyRequest(channel=TUNABLE, center_frequency_hz=1_000_000))
        self.assertEqual(ctx.exception.code(), grpc.StatusCode.ABORTED)
        self.assertIn("out_altvoltage0_RX_LO_frequency", ctx.exception.details())
        with self.assertRaises(grpc.RpcError) as ctx:
            self.control.SetSampleRate(TunableChanneld_pb2.SetSampleRateRequest(channel=TUNABLE, sample_rate_sps=1_000_000))
        self.assertEqual(ctx.exception.code(), grpc.StatusCode.ABORTED)
        self.assertIn("in_voltage_sampling_frequency", ctx.exception.details())
        self.control.SetAnalogBandwidth(TunableChanneld_pb2.SetAnalogBandwidthRequest(channel=TUNABLE, analog_bandwidth_hz=50_000_000))
        self.assertEqual(self.control.GetAnalogBandwidth(TunableChanneld_pb2.GetAnalogBandwidthRequest(channel=TUNABLE)).analog_bandwidth_hz, 20_000_000)
        with self.assertRaises(grpc.RpcError) as ctx:
            self.control.SetCenterFrequency(TunableChanneld_pb2.SetCenterFrequencyRequest(channel=FIXED_1090, center_frequency_hz=1_090_000_000))
        self.assertEqual(ctx.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)

    def test_stream_blocks(self):
        props = self.stream.GetStreamProperties(Samplestreamingd_pb2.GetStreamPropertiesRequest(radio_identification=TUNABLE))
        self.assertEqual((props.center_frequency, props.sample_rate, props.calibration_value), (1_090_000_000, 12_000_000, fake_grx.CALIBRATION_DB))
        replies = list(self.stream.StartStream(Samplestreamingd_pb2.StartStreamRequest(radio_identification=TUNABLE, requested_blocks=3)))
        self.assertEqual(len(replies), 3)
        self.assertEqual([len(r.samples) for r in replies], [fake_grx.BLOCK_SAMPLES * 4] * 3)
        self.assertEqual([r.lost_blocks for r in replies], [0, 0, 0])
        stamps = [r.block_timestamp for r in replies]
        self.assertEqual(stamps[1] - stamps[0], PERIOD_NS)
        self.assertEqual(stamps[2] - stamps[1], PERIOD_NS)
        iq = np.frombuffer(replies[0].samples, dtype="<i2").reshape(-1, 2).astype(np.float64)
        spectrum = np.abs(np.fft.fft(iq[:, 0] + 1j * iq[:, 1]))
        self.assertEqual(int(np.argmax(spectrum)), fake_grx.BLOCK_SAMPLES // fake_grx.TONE_DIVISOR)

    def test_fixed_channel(self):
        props = self.stream.GetStreamProperties(Samplestreamingd_pb2.GetStreamPropertiesRequest(radio_identification=FIXED_1090))
        self.assertEqual(props.center_frequency, 1_090_000_000)
        other = Common_pb2.RadioIdentification(band=Common_pb2.BAND_978_MHZ, per_band_index=0)
        with self.assertRaises(grpc.RpcError) as ctx:
            self.stream.GetStreamProperties(Samplestreamingd_pb2.GetStreamPropertiesRequest(radio_identification=other))
        self.assertEqual(ctx.exception.code(), grpc.StatusCode.INVALID_ARGUMENT)

    def test_1090_channel_carries_adsb_frames(self):
        from gnuradio.serorx.adsb import modes
        replies = list(self.stream.StartStream(Samplestreamingd_pb2.StartStreamRequest(radio_identification=FIXED_1090, requested_blocks=3 * fake_grx.FRAME_EVERY)))
        raw = np.frombuffer(b"".join(r.samples for r in replies), dtype="<i2").astype(np.float32)
        mag = np.abs(raw[0::2] + 1j * raw[1::2]).astype(np.float32)
        valid = []
        for s in modes.candidates(mag, 12):
            frame, confidence = modes.slice_frame(mag[s:s + 1440], 12)
            if not modes.check(frame):
                frame = modes.correct(frame, confidence) or frame
            if modes.check(frame):
                valid.append(frame.hex().upper())
        self.assertGreaterEqual(len(valid), 3)
        self.assertTrue(set(valid) <= set(fake_grx.ADSB_FRAMES), valid)

    def test_inject_lost_blocks(self):
        call = self.stream.StartStream(Samplestreamingd_pb2.StartStreamRequest(radio_identification=TUNABLE, requested_blocks=0))
        previous = next(call)
        self.fake.inject_lost_blocks(2)
        # The server generates ahead of the client, so the loss shows up a few blocks later.
        for _ in range(200):
            reply = next(call)
            if reply.lost_blocks:
                break
            self.assertEqual(reply.block_timestamp - previous.block_timestamp, PERIOD_NS)
            previous = reply
        after = next(call)
        call.cancel()
        self.assertEqual(reply.lost_blocks, 2)
        self.assertEqual(reply.block_timestamp - previous.block_timestamp, 3 * PERIOD_NS)
        self.assertEqual(after.lost_blocks, 2)
        self.assertEqual(after.block_timestamp - reply.block_timestamp, PERIOD_NS)

    def test_set_sample_rate_ends_stream(self):
        call = self.stream.StartStream(Samplestreamingd_pb2.StartStreamRequest(radio_identification=TUNABLE, requested_blocks=0))
        next(call)
        ended = threading.Event()

        def drain():
            for _ in call:
                pass
            ended.set()

        threading.Thread(target=drain, daemon=True).start()
        t0 = time.monotonic()
        self.control.SetSampleRate(TunableChanneld_pb2.SetSampleRateRequest(channel=TUNABLE, sample_rate_sps=6_000_000))
        self.assertGreaterEqual(time.monotonic() - t0, fake_grx.STALL_SECONDS)
        self.assertTrue(ended.wait(2.0))
        props = self.stream.GetStreamProperties(Samplestreamingd_pb2.GetStreamPropertiesRequest(radio_identification=TUNABLE))
        self.assertEqual(props.sample_rate, 6_000_000)


if __name__ == "__main__":
    gr_unittest.run(qa_fake_grx)
