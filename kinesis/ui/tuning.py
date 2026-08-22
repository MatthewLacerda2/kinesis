"""Live tuning panel (toggle with T). Values persist to ~/.config/kinesis/tuning.json.

Every gesture constant is edited here and nowhere else -- nothing that appears
in this panel may be hardcoded elsewhere in the app.
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QDockWidget,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..tracking.protocol import Tuning
from .overlay import hand_color

CONFIG_PATH = Path.home() / ".config" / "kinesis" / "tuning.json"

# name -> (label, min, max, step, decimals)
SLIDERS = [
    ("pinch_close", "Pinch close  (grab below)", 0.04, 0.80, 0.002, 3),
    ("pinch_open", "Pinch open  (release above)", 0.06, 1.20, 0.002, 3),
    ("min_cutoff", "One Euro  min_cutoff  (lower = smoother)", 0.10, 8.00, 0.05, 2),
    ("beta", "One Euro  beta  (higher = less lag)", 0.000, 0.300, 0.001, 3),
    ("rect_x0", "Active rect  left", 0.00, 0.49, 0.01, 2),
    ("rect_x1", "Active rect  right", 0.51, 1.00, 0.01, 2),
    ("rect_y0", "Active rect  top", 0.00, 0.49, 0.01, 2),
    ("rect_y1", "Active rect  bottom", 0.51, 1.00, 0.01, 2),
    ("lerp_alpha", "UI lerp alpha  (1.0 = no extra lag)", 0.05, 1.00, 0.05, 2),
    ("lost_hold_ms", "Hold on lost hands (ms)", 0.0, 1000.0, 25.0, 0),
    ("box_grab_band", "Box border grab width (scene units)", 4.0, 200.0, 2.0, 0),
]


def load_tuning() -> Tuning:
    try:
        return Tuning.from_dict(json.loads(CONFIG_PATH.read_text()))
    except (OSError, ValueError):
        return Tuning()


def save_tuning(tuning: Tuning) -> None:
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = tuning.to_dict()
        data.pop("preview", None)  # session state, not a tuned value
        CONFIG_PATH.write_text(json.dumps(data, indent=2))
    except OSError:
        pass


class RatioPlot(QFrame):
    """Rolling plot of pinch_ratio per hand, with the two thresholds marked."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(90)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.history: dict[str, deque] = {}
        self.close_t = 0.30
        self.open_t = 0.45

    def push(self, hands, close_t: float, open_t: float) -> None:
        self.close_t, self.open_t = close_t, open_t
        seen = set()
        for hand in hands:
            seen.add(hand.handedness)
            buf = self.history.setdefault(hand.handedness, deque(maxlen=240))
            buf.append(hand.pinch_ratio)
        for label, buf in self.history.items():
            if label not in seen:
                buf.append(None)
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        rect = self.rect().adjusted(2, 2, -2, -2)
        top = 1.2  # ratio at the top of the plot

        def y_for(value: float) -> float:
            return rect.bottom() - (min(value, top) / top) * rect.height()

        painter.fillRect(rect, QColor(24, 25, 29))
        for value, color, text in ((self.close_t, QColor(120, 200, 130), "close"),
                                   (self.open_t, QColor(220, 140, 120), "open")):
            y = y_for(value)
            painter.setPen(QPen(color, 1, Qt.PenStyle.DashLine))
            painter.drawLine(rect.left(), int(y), rect.right(), int(y))
            painter.setPen(color)
            painter.drawText(rect.left() + 4, int(y) - 2, text)

        for label, buf in self.history.items():
            painter.setPen(QPen(hand_color(label), 1.5))
            prev = None
            n = len(buf)
            for i, value in enumerate(buf):
                if value is None:
                    prev = None
                    continue
                x = rect.left() + (i / max(1, n - 1)) * rect.width()
                y = y_for(value)
                if prev is not None:
                    painter.drawLine(int(prev[0]), int(prev[1]), int(x), int(y))
                prev = (x, y)
        painter.end()


class TuningPanel(QDockWidget):
    tuning_changed = Signal(Tuning)

    def __init__(self, tuning: Tuning, parent=None):
        super().__init__("Tuning", parent)
        self.tuning = tuning
        self._updating = False
        self.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea
                             | Qt.DockWidgetArea.LeftDockWidgetArea)

        body = QWidget()
        outer = QVBoxLayout(body)

        self.readout = QLabel("tracking off")
        self.readout.setTextFormat(Qt.TextFormat.RichText)
        outer.addWidget(self.readout)

        self.plot = RatioPlot()
        outer.addWidget(self.plot)

        form = QFormLayout()
        self.widgets: dict[str, tuple[QSlider, QDoubleSpinBox]] = {}
        for name, label, lo, hi, step, decimals in SLIDERS:
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setMinimum(int(lo / step))
            slider.setMaximum(int(hi / step))
            spin = QDoubleSpinBox()
            spin.setRange(lo, hi)
            spin.setSingleStep(step)
            spin.setDecimals(decimals)

            row = QWidget()
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(1)
            row_layout.addWidget(slider)
            row_layout.addWidget(spin)

            slider.valueChanged.connect(
                lambda v, n=name, s=step: self._on_slider(n, v * s))
            spin.valueChanged.connect(lambda v, n=name: self._on_spin(n, v))
            form.addRow(label, row)
            self.widgets[name] = (slider, spin)
        outer.addLayout(form)

        reset = QPushButton("Reset to defaults")
        reset.clicked.connect(self._reset)
        outer.addWidget(reset)
        outer.addStretch(1)

        self.setWidget(body)
        self._load_into_widgets()

    # ---------- wiring ----------

    def _load_into_widgets(self) -> None:
        self._updating = True
        for name, _, _, _, step, _ in SLIDERS:
            value = getattr(self.tuning, name)
            slider, spin = self.widgets[name]
            slider.setValue(int(round(value / step)))
            spin.setValue(value)
        self._updating = False

    def _on_slider(self, name: str, value: float) -> None:
        if self._updating:
            return
        self._updating = True
        self.widgets[name][1].setValue(value)
        self._updating = False
        self._commit(name, value)

    def _on_spin(self, name: str, value: float) -> None:
        if self._updating:
            return
        step = next(s for n, _, _, _, s, _ in SLIDERS if n == name)
        self._updating = True
        self.widgets[name][0].setValue(int(round(value / step)))
        self._updating = False
        self._commit(name, value)

    def _commit(self, name: str, value: float) -> None:
        setattr(self.tuning, name, value)
        # Keep the Schmitt trigger well-formed: open must stay above close.
        if self.tuning.pinch_open <= self.tuning.pinch_close:
            self.tuning.pinch_open = self.tuning.pinch_close + 0.02
            self._load_into_widgets()
        save_tuning(self.tuning)
        self.tuning_changed.emit(self.tuning)

    def _reset(self) -> None:
        preview = self.tuning.preview
        self.tuning = Tuning(preview=preview)
        self._load_into_widgets()
        save_tuning(self.tuning)
        self.tuning_changed.emit(self.tuning)

    # ---------- live feedback ----------

    def update_live(self, frame, fps: float, latency_ms: float) -> None:
        hands = frame.hands if frame else []
        self.plot.push(hands, self.tuning.pinch_close, self.tuning.pinch_open)
        if not hands:
            self.readout.setText(
                f"<b>no hands</b> — {fps:.0f} fps, {latency_ms:.0f} ms latency")
            return
        parts = []
        for hand in hands:
            color = hand_color(hand.handedness).name()
            state = "PINCH" if hand.pinching else "open"
            # The filter lag sits next to the sliders that set it, so beta can
            # be tuned against the number it moves instead of by feel alone.
            parts.append(
                f"<span style='color:{color}'><b>{hand.handedness}</b> "
                f"{hand.pinch_ratio:.3f} {state}, {hand.group_delay_ms:.0f} ms filter</span>")
        parts.append(f"{fps:.0f} fps, {latency_ms:.0f} ms")
        self.readout.setText(" &nbsp;|&nbsp; ".join(parts))
