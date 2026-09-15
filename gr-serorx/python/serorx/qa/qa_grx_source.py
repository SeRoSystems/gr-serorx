import contextlib
import importlib
import re
import threading
import time
from unittest import mock

import numpy as np
import pmt
from gnuradio import blocks, gr, gr_unittest

import fake_grx
from gnuradio.serorx import grx_client
from gnuradio.serorx.grx_decode import STREAM_NAMES
from gnuradio.serorx.grx_source import (TAG_CALIBRATION, TAG_DECODE_PREFIX, TAG_FREQ, TAG_LOST, TAG_RATE, TAG_TIME,
                                                TAG_TIMESTAMP, grx_source)
from gnuradio.serorx.proto import Receiverd_pb2

MODULE = importlib.import_module("gnuradio.serorx.grx_source")
N = fake_grx.BLOCK_SAMPLES * 4
RECEIVER_LINE = ("info", f"receiver {fake_grx.MODEL}, serial {fake_grx.SERIAL}, hardware {fake_grx.HARDWARE}, "
                         f"image {fake_grx.IMAGE}")
CHANNELS_LINE = ("info", "channels: 1030 (index 0), 1090 (index 0), tunable (index 0)")
SUMMARY = re.compile(r"stream stopped: (\d+) blocks delivered, (\d+) lost \((\d+\.\d) %\): "
                     r"(\d+) reported by receiver, (\d+) dropped by full buffer")


def tags_of(sink):
    return {pmt.symbol_to_string(t.key): (pmt.to_python(t.value), t.offset) for t in sink.tags()}


class qa_grx_source(gr_unittest.TestCase):
    def setUp(self):
        self.fake = fake_grx.FakeGrx(realtime=False).start()

    def tearDown(self):
        self.fake.stop()

    def source(self, **kwargs):
        args = dict(host="127.0.0.1", control_port=self.fake.control_port, stream_port=self.fake.stream_port,
                    monitor_port=self.fake.monitor_port, decode_port=self.fake.decode_port, timeout=2.0)
        args.update(kwargs)
        return grx_source(**args)

    @contextlib.contextmanager
    def console(self):
        """Collects every (level, text) the block sends to its logger inside the with block."""
        lines = []
        with mock.patch.object(grx_source, "_log", side_effect=lambda level, text: lines.append((level, text))):
            yield lines

    def run_head(self, src, count, itemsize=gr.sizeof_gr_complex, sink=None):
        tb = gr.top_block()
        sink = sink or blocks.vector_sink_c()
        tb.connect(src, blocks.head(itemsize, count), sink)
        tb.run()
        return sink

    def test_float_output_is_scaled_tone(self):
        sink = self.run_head(self.source(), N)
        data = np.array(sink.data(), dtype=np.complex64)
        self.assertEqual(len(data), N)
        self.assertAlmostEqual(float(np.mean(np.abs(data))), 1000 / 32768, delta=0.004)
        peak = int(np.argmax(np.abs(np.fft.fft(data[:fake_grx.BLOCK_SAMPLES]))))
        self.assertEqual(peak, fake_grx.BLOCK_SAMPLES // fake_grx.TONE_DIVISOR)

    def test_int16_output_is_raw(self):
        sink = self.run_head(self.source(output_type="sc16"), N, itemsize=4, sink=blocks.vector_sink_s(2))
        data = np.array(sink.data(), dtype=np.int16).reshape(-1, 2)
        self.assertEqual(len(data), N)
        self.assertAlmostEqual(float(np.mean(np.hypot(data[:, 0], data[:, 1]))), 1000, delta=120)

    def test_start_tags(self):
        tags = tags_of(self.run_head(self.source(gain=10), N))
        self.assertEqual(tags[TAG_RATE], (12e6, 0))
        self.assertEqual(tags[TAG_FREQ], (1090e6, 0))
        self.assertEqual(tags[TAG_CALIBRATION], (fake_grx.CALIBRATION_DB - 10, 0))
        self.assertEqual(tags[TAG_TIMESTAMP][1], 0)
        ns = tags[TAG_TIMESTAMP][0]
        secs, frac = tags[TAG_TIME][0]
        self.assertEqual(tags[TAG_TIME][1], 0)
        self.assertEqual(secs, ns // 1_000_000_000)
        self.assertAlmostEqual(frac, (ns % 1_000_000_000) / 1e9, places=9)
        self.assertNotIn(TAG_LOST, tags)

    def test_settings_applied_in_constructor(self):
        src = self.source(center_freq=900e6, samp_rate=6e6, gain=20, rx_port="narrow", bandwidth=2e6)
        self.assertEqual(self.fake.settings, {"rx_port": 1, "center_frequency": 900_000_000, "sample_rate": 6_000_000,
                                              "bandwidth": 2_000_000, "gain": 20})
        self.assertEqual(src.get_samp_rate(), 6_000_000)

    def test_bandwidth_zero_is_unchanged(self):
        self.source()
        self.assertEqual(self.fake.settings["bandwidth"], 12_000_000)

    def test_fixed_channel_skips_control(self):
        tags = tags_of(self.run_head(self.source(band="1090", center_freq=1e6, gain=99), N))
        self.assertEqual(tags[TAG_FREQ], (1090e6, 0))
        self.assertEqual(self.fake.settings["gain"], 0)

    def test_invalid_argument_fails_construction(self):
        with self.console() as lines, self.assertRaises(RuntimeError) as ctx:
            self.source(center_freq=1e6)
        message = "center frequency 1.00 MHz rejected by receiver: ABORTED: Cannot write to sysfs file (out_altvoltage0_RX_LO_frequency): Invalid argument. Selected RX input (Wide) supports 325 to 3800 MHz"
        self.assertEqual(str(ctx.exception), message)
        self.assertEqual(lines[-1], ("error", message))

    def test_protocol_limits(self):
        src = self.source()
        with self.console() as lines:
            self.assertFalse(src.set_gain(-5))
            self.assertFalse(src.set_center_freq(5e9))
        self.assertEqual(lines, [
            ("error", "hardware gain -5 dB rejected: Value out of range: -5. Receiver supports 0 to 70 dB"),
            ("error", "center frequency 5000.00 MHz rejected: Value out of range: 5000000000. "
                      "Selected RX input (Wide) supports 325 to 3800 MHz")])
        self.assertEqual(src.get_gain(), 0)
        self.assertEqual(src.get_center_freq(), 1_090_000_000)
        with self.assertRaises(RuntimeError) as ctx:
            self.source(gain=-1)
        self.assertEqual(str(ctx.exception), "hardware gain -1 dB rejected: Value out of range: -1. Receiver supports 0 to 70 dB")

    def test_hint_follows_rx_input(self):
        src = self.source(rx_port="narrow")
        with self.console() as lines:
            self.assertFalse(src.set_center_freq(1))
        self.assertTrue(lines[0][1].endswith("Selected RX input (Narrow) supports 700 to 1100 MHz"), lines)

    def test_bandwidth_clamp_warns(self):
        with self.console() as lines:
            src = self.source(bandwidth=50e6)
        self.assertEqual(lines[-1], ("warn", "analog bandwidth requested 50.00 MHz, receiver reports 20.00 MHz"))
        self.assertEqual(src.get_bandwidth(), 20_000_000)

    def test_rx_input_not_found(self):
        with mock.patch.dict(fake_grx.RX_PORTS, {0: "Main", 1: "Aux"}, clear=True), self.assertRaises(RuntimeError) as ctx:
            self.source()
        self.assertEqual(str(ctx.exception), "RX input 'wide' not found, receiver offers Main, Aux")

    def test_unreachable_fails_construction(self):
        with self.console() as lines, self.assertRaises(RuntimeError) as ctx:
            self.source(control_port=1, timeout=0.5)
        message = ("host '127.0.0.1' answers, but port 1 (TunableChanneld) is closed, check address and firewall. "
                   "Tunable channel cannot be configured")
        self.assertEqual(str(ctx.exception), message)
        self.assertEqual(lines[-1], ("error", message))

    def test_unreachable_host_fails_construction(self):
        with self.console() as lines, self.assertRaises(RuntimeError) as ctx:
            self.source(host="192.0.2.1", timeout=0.5)
        message = str(ctx.exception)
        self.assertTrue(message.startswith("host '192.0.2.1' could not be reached ("), message)
        self.assertTrue(message.endswith("), check address and receiver connection"), message)
        self.assertEqual(lines, [("error", message)])

    def test_unresolved_host_fails_construction(self):
        # The probe is patched: a resolver that answers for any name would reach the refused branch.
        with mock.patch.object(MODULE, "reachability", return_value="unresolved"):
            with self.assertRaises(RuntimeError) as ctx:
                self.source(host="grx.invalid", timeout=0.5)
        self.assertEqual(str(ctx.exception), "host 'grx.invalid' does not resolve, check address")

    def test_console_receiver_and_settings(self):
        with self.console() as lines:
            self.source(gain=20, bandwidth=2e6)
        self.assertEqual(lines, [RECEIVER_LINE, CHANNELS_LINE,
                                 ("info", "channel tunable (index 0): receiver reports 1090.00 MHz, 12.00 MSps, "
                                          "hardware gain 20 dB, analog bandwidth 2.00 MHz, RX input Wide, calibration -120.00 dB")])

    def test_console_fixed_channel(self):
        with self.console() as lines:
            self.source(band="1030")
        self.assertEqual(lines, [RECEIVER_LINE, CHANNELS_LINE,
                                 ("info", "channel 1030 (index 0): receiver reports 1030.00 MHz, 12.00 MSps, "
                                          "calibration -100.00 dB")])

    def test_unavailable_channel_lists_channels(self):
        with self.console() as lines, self.assertRaises(RuntimeError) as ctx:
            self.source(band="978")
        message = "channel 978 (index 0) is not available. Available channels: 1030 (index 0), 1090 (index 0), tunable (index 0)"
        self.assertEqual(str(ctx.exception), message)
        self.assertEqual(lines[-1], ("error", message))

    def test_unreachable_stream_fails_construction(self):
        with self.console() as lines, self.assertRaises(RuntimeError) as ctx:
            self.source(stream_port=1, timeout=0.5)
        message = "host '127.0.0.1' answers, but port 1 (Samplestreamingd) is closed, check address and firewall"
        self.assertEqual(str(ctx.exception), message)
        self.assertEqual(lines, [("error", message)])

    def test_unreachable_monitor_only_warns(self):
        with self.console() as lines:
            self.source(monitor_port=1, timeout=0.5)
        self.assertEqual(lines[0], ("warn", "host '127.0.0.1' answers, but port 1 (Monitord) is closed, check address "
                                             "and firewall. Model and serial unknown"))
        self.assertEqual(lines[1], CHANNELS_LINE)
        self.assertTrue(lines[2][1].startswith("channel tunable (index 0): receiver reports "), lines[2])

    def test_constructor_warns_on_coerced_frequency(self):
        with self.console() as lines:
            src = self.source(center_freq=1090000500)
        self.assertEqual(lines[-1], ("warn", "center frequency requested 1090.00 MHz, receiver reports 1090.00 MHz (-500 Hz)"))
        self.assertEqual(src.get_center_freq(), 1090000000)
        self.assertEqual(tags_of(self.run_head(src, N))[TAG_FREQ], (1090e6, 0))

    def test_setter_warns_on_coerced_frequency(self):
        src = self.source()
        with self.console() as lines:
            self.assertTrue(src.set_center_freq(950000500))
        self.assertEqual(lines, [("warn", "center frequency requested 950.00 MHz, receiver reports 950.00 MHz (-500 Hz)")])
        self.assertEqual(src.get_center_freq(), 950000000)

    def test_bad_parameters(self):
        with self.assertRaises(ValueError):
            self.source(output_type="fc64")
        with self.assertRaises(ValueError):
            self.source(rx_port="both")

    def test_throughput_12msps_no_local_drops(self):
        src = self.source()
        counter = blocks.tag_debug(gr.sizeof_gr_complex, "lost", TAG_LOST)
        counter.set_display(False)
        tb = gr.top_block()
        tb.connect(src, blocks.head(gr.sizeof_gr_complex, 24_000_000), counter)
        t0 = time.monotonic()
        tb.run()
        self.assertEqual(counter.num_tags(), 0)
        self.assertLess(time.monotonic() - t0, 10.0)


    def run_for(self, src, seconds, actions=()):
        """Runs src into a sink for seconds, calling each (delay, callable) from actions."""
        tb = gr.top_block()
        sink = blocks.vector_sink_c()
        tb.connect(src, sink)
        tb.start()
        t0 = time.monotonic()
        for delay, action in actions:
            time.sleep(max(0.0, t0 + delay - time.monotonic()))
            action()
        time.sleep(max(0.0, t0 + seconds - time.monotonic()))
        tb.stop()
        tb.wait()
        return sink

    def realtime_fake(self):
        self.fake.stop()
        self.fake = fake_grx.FakeGrx(realtime=True).start()

    def test_lost_blocks_tag(self):
        self.realtime_fake()
        src = self.source(samp_rate=2.5e6)
        sink = self.run_for(src, 1.0, [(0.3, lambda: self.fake.inject_lost_blocks(3))])
        tags = tags_of(sink)
        self.assertEqual(tags[TAG_LOST][0], 3)
        self.assertGreater(tags[TAG_LOST][1], 0)
        stamps = [t for t in sink.tags() if pmt.symbol_to_string(t.key) == TAG_TIMESTAMP]
        times = [t for t in sink.tags() if pmt.symbol_to_string(t.key) == TAG_TIME]
        self.assertEqual(len(stamps), 2)
        self.assertEqual(len(times), 2)
        self.assertEqual(stamps[1].offset, tags[TAG_LOST][1])
        self.assertEqual(times[1].offset, tags[TAG_LOST][1])

    def test_setters_tag_and_apply(self):
        self.realtime_fake()
        with self.console() as lines, mock.patch.object(MODULE, "REFRESH_INTERVAL", 0.3):
            src = self.source(samp_rate=2.5e6)
            sink = self.run_for(src, 1.2, [(0.2, lambda: self.assertTrue(src.set_center_freq(950e6))),
                                           (0.3, lambda: self.assertTrue(src.set_gain(7))),
                                           (0.4, lambda: self.assertTrue(src.set_rx_port("narrow"))),
                                           (0.5, lambda: self.assertTrue(src.set_bandwidth(500e3)))])
        infos = [text for level, text in lines if level == "info"][3:-1]
        self.assertEqual([text for text in infos if not text.startswith("calibration")],
                         ["center frequency 950.00 MHz", "hardware gain 7 dB", "RX input Narrow (LNA)", "analog bandwidth 500.00 kHz"])
        self.assertIn("calibration -107.00 dB", infos)
        freq_tags = [pmt.to_python(t.value) for t in sink.tags() if pmt.symbol_to_string(t.key) == TAG_FREQ]
        self.assertEqual(freq_tags, [1090e6, 950e6])
        calibration_tags = [pmt.to_python(t.value) for t in sink.tags() if pmt.symbol_to_string(t.key) == TAG_CALIBRATION]
        self.assertEqual(calibration_tags, [fake_grx.CALIBRATION_DB, fake_grx.CALIBRATION_DB - 7])
        self.assertEqual(self.fake.settings, {"rx_port": 1, "center_frequency": 950_000_000, "sample_rate": 2_500_000,
                                              "bandwidth": 500_000, "gain": 7})
        self.assertEqual(src.get_center_freq(), 950_000_000)
        self.assertEqual(src.get_gain(), 7)
        self.assertEqual(src.get_rx_port(), "narrow")
        self.assertEqual(src.get_bandwidth(), 500_000)
        self.assertEqual(src.get_calibration_db(), fake_grx.CALIBRATION_DB - 7)

    def test_setter_invalid_keeps_value(self):
        src = self.source()
        with self.console() as lines:
            self.assertFalse(src.set_center_freq(1))
        self.assertEqual(lines, [("error", "center frequency 1.00 Hz rejected by receiver: ABORTED: Cannot write to sysfs file (out_altvoltage0_RX_LO_frequency): Invalid argument. Selected RX input (Wide) supports 325 to 3800 MHz")])
        self.assertEqual(src.get_center_freq(), 1_090_000_000)
        self.assertEqual(self.fake.settings["center_frequency"], 1_090_000_000)

    def test_set_samp_rate_reconnects(self):
        self.realtime_fake()
        with self.console() as lines:
            src = self.source(samp_rate=2.5e6)
            sink = self.run_for(src, 1.5, [(0.3, lambda: self.assertTrue(src.set_samp_rate(4e6)))])
        self.assertIn(("info", "sample rate 4.00 MSps"), lines)
        rate_tags = [(pmt.to_python(t.value), t.offset) for t in sink.tags() if pmt.symbol_to_string(t.key) == TAG_RATE]
        self.assertEqual([r for r, _ in rate_tags], [2.5e6, 4e6])
        self.assertGreater(len(sink.data()), rate_tags[1][1] + 10_000)
        self.assertEqual(src.get_samp_rate(), 4_000_000)
        restart = rate_tags[1][1]
        for key in (TAG_TIME, TAG_TIMESTAMP, TAG_FREQ, TAG_CALIBRATION):
            offsets = [t.offset for t in sink.tags() if pmt.symbol_to_string(t.key) == key]
            self.assertEqual(offsets, [0, restart], key)

    def test_reconnect_resets_device_counter(self):
        self.realtime_fake()
        src = self.source(samp_rate=2.5e6)
        sink = self.run_for(src, 1.5, [(0.2, lambda: self.fake.inject_lost_blocks(2)),
                                       (0.7, lambda: self.assertTrue(src.set_samp_rate(4e6)))])
        lost = [pmt.to_python(t.value) for t in sink.tags() if pmt.symbol_to_string(t.key) == TAG_LOST]
        self.assertEqual(lost, [2])

    def test_loss_report_and_summary(self):
        self.realtime_fake()
        with self.console() as lines, mock.patch.object(MODULE, "REPORT_INTERVAL", 0.5):
            src = self.source(samp_rate=2.5e6)
            self.run_for(src, 1.5, [(0.3, lambda: self.fake.inject_lost_blocks(3))])
        warnings = [text for level, text in lines if level == "warn"]
        self.assertEqual(len(warnings), 1, warnings)
        self.assertRegex(warnings[0], r"^lost 3 blocks in last \d+ s: "
                                      r"3 reported by receiver, 0 dropped by full buffer$")
        self.assertEqual(lines[-1][0], "info")
        match = SUMMARY.fullmatch(lines[-1][1])
        self.assertIsNotNone(match, lines[-1])
        delivered, lost, percent, link, buffer = match.groups()
        self.assertGreater(int(delivered), 50)
        self.assertEqual((lost, link, buffer), ("3", "3", "0"))
        self.assertAlmostEqual(float(percent), 300.0 / (int(delivered) + 3), delta=0.06)

    def test_full_buffer_drops_are_reported(self):
        self.realtime_fake()
        with self.console() as lines, mock.patch.object(MODULE, "REPORT_INTERVAL", 0.5):
            src = self.source(samp_rate=2.5e6, buffer_seconds=0.1)
            tb = gr.top_block()
            tb.connect(src, blocks.throttle(gr.sizeof_gr_complex, 10e3), blocks.null_sink(gr.sizeof_gr_complex))
            tb.start()
            time.sleep(1.5)
            tb.stop()
            tb.wait()
        self.assertTrue(any(level == "warn" and "dropped by full buffer" in text for level, text in lines), lines)
        match = SUMMARY.fullmatch(lines[-1][1])
        self.assertIsNotNone(match, lines[-1])
        self.assertGreater(int(match.group(5)), 0)
        self.assertEqual(match.group(2), match.group(5))
        self.assertIn(("warn", "flowgraph consumes slower than 2.50 MSps, samples dropped"), lines)

    def test_stream_lost_and_resumed(self):
        self.realtime_fake()
        ports = (self.fake.control_port, self.fake.stream_port, self.fake.monitor_port)

        def restart():
            self.fake = fake_grx.FakeGrx(*ports, realtime=True)
            self.fake.state.sample_rate = 2_500_000
            self.fake.start()

        with self.console() as lines:
            src = self.source(samp_rate=2.5e6)
            self.run_for(src, 5.0, [(0.5, self.fake.stop), (1.2, restart)])
        stream_lines = [text for level, text in lines if text.startswith("stream from")]
        self.assertGreaterEqual(len(stream_lines), 2, lines)
        self.assertRegex(stream_lines[0], r"^stream from \'127\.0\.0\.1\' (lost \(.+\)|ended), reconnecting in 1 s$")
        self.assertIn(("info", "stream from '127.0.0.1' resumed"), lines)

    def test_stall_is_reported(self):
        self.realtime_fake()
        with self.console() as lines, mock.patch.object(MODULE, "STALL_SECONDS", 0.5), \
                mock.patch.object(MODULE, "STALL_REPORT", 0.8):
            src = self.source(samp_rate=2.5e6)
            self.run_for(src, 2.5, [(0.3, self.fake.pause_stream)])
        stalls = [text for level, text in lines if level == "warn" and text.startswith("no samples")]
        self.assertGreaterEqual(len(stalls), 2, lines)
        self.assertRegex(stalls[0], r"^no samples from \'127\.0\.0\.1\' for \d+\.\d s$")

    def test_setter_with_receiver_gone(self):
        src = self.source()
        self.fake.stop()
        with self.console() as lines:
            self.assertFalse(src.set_gain(5))
        self.assertEqual(len(lines), 1, lines)
        self.assertEqual(lines[0][0], "error")
        self.assertRegex(lines[0][1], r"^hardware gain not set: host '127\.0\.0\.1' (answers, but port \d+ \(TunableChanneld\) is closed"
                                      r"|could not be reached \(.+\))$")

    def test_rate_not_adopted_line(self):
        src = self.source()
        src._timeout = 0.2
        stale = grx_client.StreamProperties(1_090_000_000, 12_000_000, -100.0)
        with self.console() as lines, mock.patch.object(src._stream, "properties", return_value=stale):
            props = src._wait_for_rate(5_000_000)
        self.assertEqual(props.samp_rate, 12_000_000)
        self.assertEqual(lines, [("warn", "sample rate requested 5.00 MSps, receiver reports 12.00 MSps")])

    def test_rate_adopted_from_control_when_properties_lag(self):
        src = self.source()
        src._timeout = 0.2
        src._control.set_samp_rate(src._radio, 5_000_000)
        stale = grx_client.StreamProperties(1_090_000_000, 12_000_000, -100.0)
        with self.console() as lines, mock.patch.object(src._stream, "properties", return_value=stale):
            props = src._wait_for_rate(5_000_000)
        self.assertEqual(props.samp_rate, 5_000_000)
        self.assertEqual(lines, [("warn", "stream properties report 12.00 MSps, expected 5.00 MSps")])

    def test_refresh_tracks_calibration_only(self):
        src = self.source()
        src._pending_tags = []
        src._refresh_time = 0.0
        stale = grx_client.StreamProperties(950_000_000, 4_000_000, -90.0)
        with self.console() as lines, mock.patch.object(src._stream, "properties", return_value=stale):
            src._refresh_properties()
        self.assertEqual([(key, pmt.to_python(value)) for key, value in src._pending_tags], [(TAG_CALIBRATION, -90.0)])
        self.assertEqual(src._props, grx_client.StreamProperties(1_090_000_000, 12_000_000, -90.0))
        self.assertEqual(lines, [("info", "calibration -90.00 dB")])

    def test_fixed_channel_setters_ignored(self):
        src = self.source(band="1090")
        with self.console() as lines:
            self.assertFalse(src.set_gain(5))
            self.assertFalse(src.set_rx_port("narrow"))
        self.assertEqual(lines, [("warn", "hardware gain ignored: channel is not tunable"),
                                 ("warn", "RX input ignored: channel is not tunable")])
        self.assertEqual(self.fake.settings["gain"], 0)

    def test_no_command_port(self):
        ports = self.source().message_ports_in()
        names = [pmt.symbol_to_string(pmt.vector_ref(ports, i)) for i in range(pmt.length(ports))]
        self.assertNotIn("command", names)

    def test_work_drops_partial_sample(self):
        src = self.source()
        src._pending_tags = []
        src._start_tags = []
        with src._lock:
            src._queue.append(MODULE._Entry(b"\x00" * 6, [], 0, 1, time.monotonic()))
            src._queued_bytes = 6
        out = np.zeros(100, dtype=np.complex64)
        result = []
        worker = threading.Thread(target=lambda: result.append(src.work([], [out])), daemon=True)
        worker.start()
        worker.join(2.0)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [1])

    def test_failed_construction_closes_channels(self):
        with mock.patch.object(grx_client._Client, "close", autospec=True, side_effect=grx_client._Client.close) as close, \
                self.assertRaises(RuntimeError):
            self.source(center_freq=1e6)
        closed = {type(call.args[0]) for call in close.call_args_list}
        self.assertEqual(closed, {grx_client.GrxControl, grx_client.GrxStream, grx_client.GrxMonitor})

    def decode_tags(self, sink, name):
        """Every tag of one decode stream as (offset, fields dict)."""
        key = TAG_DECODE_PREFIX + name
        return [(tag.offset, pmt.to_python(tag.value)) for tag in sorted(sink.tags(), key=lambda tag: tag.offset)
                if pmt.symbol_to_string(tag.key) == key]

    def test_decode_tags_sit_on_their_sample(self):
        """Every tag lands where its timestamp says, measured from the timestamp tag of its own stream start."""
        self.realtime_fake()
        src = self.source(band="1090", decodes=("modes_downlink", "dme_tacan"), decode_delay=0.3, buffer_seconds=1.0)
        sink = self.run_for(src, 1.2)
        base, base_offset, checked = None, 0, 0
        for tag in sorted(sink.tags(), key=lambda tag: tag.offset):
            key = pmt.symbol_to_string(tag.key)
            if key == TAG_TIMESTAMP:
                base, base_offset = pmt.to_uint64(tag.value), tag.offset
                continue
            if not key.startswith(TAG_DECODE_PREFIX) or not pmt.is_dict(tag.value):
                continue
            fields = pmt.to_python(tag.value)
            expected = base_offset + (fields["timestamp"] - base) * 12_000_000 // 1_000_000_000
            # The fake rounds every block timestamp down to a whole nanosecond, which walks one sample per stream.
            self.assertAlmostEqual(tag.offset, expected, delta=1)
            checked += 1
        self.assertGreater(checked, 0)

    def test_decode_tag_carries_the_frame(self):
        self.realtime_fake()
        src = self.source(band="1090", decodes=("modes_downlink",), decode_delay=0.3, buffer_seconds=1.0)
        tags = self.decode_tags(self.run_for(src, 1.2), "modes_downlink")
        self.assertGreater(len(tags), 0)
        offset, fields = tags[0]
        self.assertEqual(bytes(fields["payload"]).hex().upper(), fake_grx.ADSB_FRAMES[0])
        self.assertEqual(fields["df"], 17)
        self.assertEqual(fields["level_signal"], fake_grx.SIGNAL_LEVEL)
        self.assertTrue(fields["message_valid"])
        self.assertGreater(fields["timestamp"], 0)

    def test_every_stream_tags(self):
        self.realtime_fake()
        src = self.source(band="1090", decodes=STREAM_NAMES, decode_delay=0.3, buffer_seconds=1.0)
        with self.console() as lines:
            sink = self.run_for(src, 1.2)
        for name in STREAM_NAMES:
            self.assertGreater(len(self.decode_tags(sink, name)), 0, name)
        summary = [text for level, text in lines if text.startswith("decoding stopped")]
        self.assertEqual(len(summary), 1)
        self.assertIn("Mode S downlink", summary[0])

    def test_decoding_off_adds_no_tags(self):
        sink = self.run_head(self.source(band="1090"), N)
        keys = {TAG_DECODE_PREFIX + name for name in STREAM_NAMES}
        self.assertFalse([tag for tag in sink.tags() if pmt.symbol_to_string(tag.key) in keys])

    def test_untimed_items_are_counted(self):
        self.realtime_fake()
        self.fake.set_timing_base(Receiverd_pb2.SYSTEM_TIME)
        src = self.source(band="1090", decodes=("dme_tacan",), decode_delay=0.3, buffer_seconds=1.0)
        with self.console() as lines:
            sink = self.run_for(src, 1.2)
        self.assertEqual(self.decode_tags(sink, "dme_tacan"), [])
        self.assertGreater(src._decode_lost["untimed"], 0)
        self.assertIn("without GPS time", [text for level, text in lines if text.startswith("decoding stopped")][0])

    def test_late_items_ask_for_a_longer_delay(self):
        self.realtime_fake()
        with self.console() as lines, mock.patch.object(MODULE, "REPORT_INTERVAL", 0.3):
            src = self.source(band="1090", decodes=("dme_tacan",), decode_delay=0.0, buffer_seconds=0.001)
            self.run_for(src, 1.5)
        late = [text for level, text in lines if "older than the buffer" in text and level == "warn"]
        self.assertGreater(len(late), 0, lines)
        self.assertRegex(late[0], r"raise the decode delay above \d+\.\d\d s$")

    def test_unreachable_receiverd_only_warns(self):
        with self.console() as lines:
            sink = self.run_head(self.source(band="1090", decodes=("dme_tacan",), decode_port=1, timeout=0.5), N)
        self.assertEqual(len(sink.data()), N)
        self.assertIn(("warn", "host '127.0.0.1' answers, but port 1 (Receiverd) is closed, "
                               "check address and firewall. No decode tags"), lines)

    def test_streams_the_receiver_does_not_report(self):
        from gnuradio.serorx.grx_decode import SPECS
        self.fake.set_capabilities(SPECS["modes_downlink"].capabilities)
        with self.console() as lines:
            self.source(band="1090", decodes=("modes_downlink", "mode5_replies", "uat_adsb"))
        self.assertIn(("warn", "receiver does not report Mode 5 replies, UAT ADS-B, those streams stay silent"),
                      lines)

    def test_capable_streams_pass_without_a_warning(self):
        with self.console() as lines:
            self.source(band="1090", decodes=STREAM_NAMES)
        self.assertFalse([text for level, text in lines if text.startswith("receiver does not report")], lines)

    def test_unknown_decode_stream(self):
        with self.assertRaises(ValueError) as caught:
            self.source(decodes=("nothing",))
        self.assertIn("'nothing'", str(caught.exception))

    def test_halt_before_stream_open_ends_reader(self):
        self.realtime_fake()
        src = self.source(samp_rate=2.5e6)
        start = src._stream.start

        def start_then_halt(radio):
            iterator = start(radio)
            src._stop_reader.set()
            return iterator

        with mock.patch.object(src._stream, "start", side_effect=start_then_halt):
            src.start()
            src._reader.join(1.0)
            self.assertFalse(src._reader.is_alive())
            self.assertEqual(src._blocks_in, 0)
        src.stop()


if __name__ == "__main__":
    gr_unittest.run(qa_grx_source)
