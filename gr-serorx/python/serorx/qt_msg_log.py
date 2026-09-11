"""QT GUI message log: every incoming message becomes one line in a scrolling text box."""
import pmt
from gnuradio import gr


class qt_msg_log(gr.basic_block):
    """Message sink with a Qt widget. widget() returns the text box for the flowgraph layout.

    A string appears as it is, a byte PDU as UTF-8 text, any other PMT in printed form.
    Messages arrive on GNU Radio's message thread. A Qt signal hands each line to the GUI thread.
    """

    def __init__(self, max_lines=500, title="Messages"):
        gr.basic_block.__init__(self, name="qt_msg_log", in_sig=None, out_sig=None)
        from PyQt5 import QtCore, QtGui, QtWidgets

        class Relay(QtCore.QObject):
            line = QtCore.pyqtSignal(str)

        self._relay = Relay()
        self._text = QtWidgets.QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setMaximumBlockCount(int(max_lines))
        self._text.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        self._text.setPlaceholderText(str(title))
        self._relay.line.connect(self._text.appendPlainText)
        self.message_port_register_in(pmt.intern("in"))
        self.set_msg_handler(pmt.intern("in"), self._handle)

    def widget(self):
        return self._text

    def _handle(self, msg):
        if pmt.is_symbol(msg):
            text = pmt.symbol_to_string(msg)
        elif pmt.is_pair(msg) and pmt.is_u8vector(pmt.cdr(msg)):
            text = bytes(pmt.u8vector_elements(pmt.cdr(msg))).decode("utf-8", "replace")
        else:
            text = pmt.write_string(msg)
        self._relay.line.emit(text)
