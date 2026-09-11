import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pmt
from gnuradio import blocks, gr, gr_unittest

try:
    from PyQt5 import QtWidgets
except ImportError:
    QtWidgets = None


class qa_qt_msg_log(gr_unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if QtWidgets is not None:
            cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        if QtWidgets is None:
            self.skipTest("PyQt5 is not installed")

    def test_lines_and_limit(self):
        from gnuradio.serorx import qt_msg_log
        log = qt_msg_log(max_lines=3, title="t")
        for i in range(5):
            log._handle(pmt.intern(f"line {i}"))
        log._handle(pmt.from_long(7))
        self.app.processEvents()
        self.assertEqual(log.widget().toPlainText().splitlines(), ["line 3", "line 4", "7"])
        self.assertEqual(log.widget().placeholderText(), "t")

    def test_byte_pdu_shown_as_text(self):
        from gnuradio.serorx import qt_msg_log
        log = qt_msg_log()
        log._handle(pmt.cons(pmt.PMT_NIL, pmt.init_u8vector(5, list(b"hello"))))
        self.app.processEvents()
        self.assertEqual(log.widget().toPlainText().splitlines(), ["hello"])

    def test_messages_from_flowgraph_thread(self):
        from gnuradio.serorx import qt_msg_log
        log = qt_msg_log()
        tb = gr.top_block()
        tb.msg_connect(blocks.message_strobe(pmt.intern("hello"), 20), "strobe", log, "in")
        tb.start()
        time.sleep(0.3)
        tb.stop()
        tb.wait()
        self.app.processEvents()
        lines = log.widget().toPlainText().splitlines()
        self.assertGreaterEqual(len(lines), 5)
        self.assertEqual(set(lines), {"hello"})


if __name__ == "__main__":
    gr_unittest.run(qa_qt_msg_log)
