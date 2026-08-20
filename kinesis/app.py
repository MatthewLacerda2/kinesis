"""QApplication + main window: mouse/keyboard input, hand-tracking toggle, MCP channel."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMainWindow,
    QMessageBox,
)

from .canvas.persistence import load_scene, save_scene
from .canvas.scene import BoardScene
from .canvas.view import BoardView
from .control import ControlServer
from .ui.camera_feed import CameraFeed
from .ui.hand_control import HandControl
from .ui.tuning import TuningPanel, load_tuning, save_tuning


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("kinesis")
        self.resize(1400, 900)

        self.board = BoardScene(self)
        self.view = BoardView(self.board, self)
        self.setCentralWidget(self.view)
        self.view.add_images_clicked.connect(self.add_images_dialog)

        self.scene_path: Path | None = None
        self.board.board_changed.connect(self._update_status)
        self.board.selectionChanged.connect(self._update_status)

        # Hand tracking starts OFF; the app is fully usable by mouse alone.
        self.tuning = load_tuning()
        self.hands = HandControl(self.view, self.tuning, self)
        self.hands.status_changed.connect(self._on_tracker_status)

        # Webcam background: independent of hand tracking, off by default.
        self.camera_bg = CameraFeed(self)
        self.camera_bg.frame_ready.connect(self._on_camera_frame)
        self.camera_bg.failed.connect(self._on_camera_failed)
        self.camera_bg.warning.connect(self._on_camera_warning)
        self.view.camera_button_clicked.connect(self.toggle_background)

        self.tuning_panel = TuningPanel(self.tuning, self)
        self.tuning_panel.tuning_changed.connect(self._on_tuning_changed)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.tuning_panel)
        self.tuning_panel.hide()
        self.hands.timer.timeout.connect(self._refresh_tuning_panel)

        # Local control channel for the MCP server (loopback + token).
        self.control = ControlServer(self)

        self._build_actions()
        self._update_status()
        self.statusBar().showMessage("Drop images onto the window to begin.", 4000)

    # ---------- actions ----------

    def _act(self, text: str, shortcut, slot, shortcuts=None) -> QAction:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        if shortcuts:
            action.setShortcuts([QKeySequence(s) for s in shortcuts])
        action.triggered.connect(slot)
        self.addAction(action)
        return action

    def _build_actions(self) -> None:
        menu = self.menuBar()

        file_menu = menu.addMenu("&File")
        file_menu.addAction(
            self._act("&Open Scene…", QKeySequence.StandardKey.Open, self.open_scene))
        file_menu.addAction(self._act("&Save Scene", QKeySequence.StandardKey.Save, self.save))
        file_menu.addAction(self._act("Save Scene &As…", "Ctrl+Shift+S", self.save_as))
        file_menu.addAction(self._act("Save &Packed…", "Ctrl+Shift+P", self.save_packed))
        file_menu.addSeparator()
        file_menu.addAction(self._act("&Add Images…", "Ctrl+I", self.add_images_dialog))

        edit_menu = menu.addMenu("&Edit")
        edit_menu.addAction(self._act("&Paste", QKeySequence.StandardKey.Paste, self.paste))
        edit_menu.addAction(self._act("&Duplicate", "Ctrl+D", self.duplicate_selected))
        edit_menu.addAction(self._act("&Delete", None, self.delete_selected,
                                      shortcuts=["Delete", "Backspace"]))
        edit_menu.addAction(self._act("Select &All", QKeySequence.StandardKey.SelectAll,
                                      self.select_all))
        edit_menu.addSeparator()
        edit_menu.addAction(self._act("Bring &Forward", "Ctrl+]", self.bring_forward))
        edit_menu.addAction(self._act("Send &Backward", "Ctrl+[", self.send_backward))

        view_menu = menu.addMenu("&View")
        view_menu.addAction(self._act("&Fit All", "Ctrl+0", self.view.zoom_to_fit))
        view_menu.addAction(self._act("Zoom &100%", "Ctrl+1", self.view.zoom_100))
        view_menu.addAction(self._act("Zoom &In", QKeySequence.StandardKey.ZoomIn,
                                      lambda: self.view.zoom_by(1.25)))
        view_menu.addAction(self._act("Zoom &Out", QKeySequence.StandardKey.ZoomOut,
                                      lambda: self.view.zoom_by(1 / 1.25)))
        view_menu.addSeparator()
        self.track_action = self._act("Hand &Tracking", "H", self.toggle_tracking)
        self.track_action.setCheckable(True)
        self.tuning_action = self._act("&Tuning Panel", "T", self.toggle_tuning)
        self.tuning_action.setCheckable(True)
        self.preview_action = self._act("Camera &Preview", "P", self.toggle_preview)
        self.preview_action.setCheckable(True)
        self.background_action = self._act("Camera &Background", "B", self.toggle_background)
        self.background_action.setCheckable(True)
        view_menu.addAction(self.track_action)
        view_menu.addAction(self.tuning_action)
        view_menu.addAction(self.preview_action)
        view_menu.addAction(self.background_action)

    # ---------- webcam background ----------

    def set_background(self, enabled: bool) -> bool:
        """Turn the webcam background on or off. Returns the resulting state."""
        if enabled and not self.camera_bg.active:
            self.camera_bg.start()
            self.statusBar().showMessage("Starting camera background…", 3000)
        elif not enabled and self.camera_bg.active:
            self.camera_bg.stop()
            self.statusBar().showMessage("Camera background off.", 3000)
        self._sync_background_state()
        return self.camera_bg.active

    def toggle_background(self) -> bool:
        return self.set_background(not self.camera_bg.active)

    def _sync_background_state(self) -> None:
        on = self.camera_bg.active
        self.background_action.setChecked(on)
        self.view.chrome.set_camera_on(on)

    def _on_camera_frame(self) -> None:
        self.view.chrome.set_background_image(self.camera_bg.latest())

    def _on_camera_warning(self, message: str) -> None:
        """Feed is up but not as asked — say so, don't interrupt."""
        self.statusBar().showMessage(message, 8000)

    def _on_camera_failed(self, message: str) -> None:
        self._sync_background_state()
        self.statusBar().showMessage(message, 0)
        QMessageBox.warning(self, "Camera background", message)

    # ---------- hand tracking ----------

    def toggle_tracking(self) -> None:
        self.hands.toggle()
        self.track_action.setChecked(self.hands.active)
        if self.hands.active:
            self.statusBar().showMessage("Starting camera — hands appear in a moment…", 4000)

    def toggle_tuning(self) -> None:
        visible = not self.tuning_panel.isVisible()
        self.tuning_panel.setVisible(visible)
        self.tuning_action.setChecked(visible)

    def toggle_preview(self) -> None:
        self.tuning.preview = not self.tuning.preview
        self.preview_action.setChecked(self.tuning.preview)
        self.hands.push_tuning(self.tuning)

    def _on_tuning_changed(self, tuning) -> None:
        self.tuning = tuning
        self.hands.push_tuning(tuning)

    def _on_tracker_status(self, state: str, message: str) -> None:
        self.track_action.setChecked(self.hands.active)
        if state == "error":
            self.statusBar().showMessage(message, 0)
            QMessageBox.warning(self, "Hand tracking", message)
        elif state == "warning":
            # Tracking is still running; a modal here would be worse than useless.
            self.statusBar().showMessage(message, 8000)
        elif state == "running":
            self.statusBar().showMessage("Hand tracking on — pinch to grab an image.", 4000)
        elif state == "stopped":
            self.statusBar().showMessage("Hand tracking off.", 3000)
            self._update_status()

    def _refresh_tuning_panel(self) -> None:
        if self.tuning_panel.isVisible():
            self.tuning_panel.update_live(self.hands.latest, self.hands.fps,
                                          self.view.chrome.latency_ms)

    def closeEvent(self, event) -> None:
        save_tuning(self.tuning)
        self.camera_bg.stop()
        self.hands.stop()
        self.control.shutdown()
        super().closeEvent(event)

    # ---------- file ----------

    def open_scene(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open scene", "", "kinesis scene (*.kinesis)")
        if not path:
            return
        try:
            loaded, missing = load_scene(self.board, path, self.view)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Could not open scene", str(exc))
            return
        self.scene_path = Path(path)
        self.setWindowTitle(f"kinesis — {Path(path).name}")
        msg = f"Loaded {loaded} image(s)."
        if missing:
            msg += f" {len(missing)} missing: {', '.join(Path(m).name for m in missing[:3])}"
        self.statusBar().showMessage(msg, 6000)
        self._update_status()

    def save(self) -> None:
        if self.scene_path is None:
            self.save_as()
            return
        save_scene(self.board, self.scene_path, self.view)
        self.statusBar().showMessage(f"Saved {self.scene_path.name}", 3000)

    def save_as(self, pack: bool = False) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save scene", "board.kinesis",
                                              "kinesis scene (*.kinesis)")
        if not path:
            return
        self.scene_path = save_scene(self.board, path, self.view, pack=pack)
        self.setWindowTitle(f"kinesis — {self.scene_path.name}")
        self.statusBar().showMessage(
            f"Saved {self.scene_path.name}" + (" (packed)" if pack else ""), 3000)

    def save_packed(self) -> None:
        self.save_as(pack=True)

    def add_images_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add images", "",
            "Images (*.png *.jpg *.jpeg *.webp *.gif *.bmp *.tif *.tiff)")
        added = 0
        for path in paths:
            try:
                self.board.add_image(path)
                added += 1
            except (OSError, ValueError):
                continue
        if added:
            self.statusBar().showMessage(f"Added {added} image(s)", 3000)

    # ---------- edit ----------

    def paste(self) -> None:
        mime = QGuiApplication.clipboard().mimeData()
        center = self.view.mapToScene(self.view.viewport().rect().center())
        if mime.hasImage():
            image = mime.imageData()
            if image is not None and not image.isNull():
                self.board.add_qimage(image, center)
                self.statusBar().showMessage("Pasted image from clipboard", 3000)
                return
        if mime.hasUrls():
            added = 0
            for url in mime.urls():
                local = url.toLocalFile()
                if not local:
                    continue
                try:
                    self.board.add_image(local)
                    added += 1
                except (OSError, ValueError):
                    continue
            if added:
                self.statusBar().showMessage(f"Pasted {added} image(s)", 3000)
                return
        self.statusBar().showMessage("Nothing pasteable on the clipboard", 3000)

    def delete_selected(self) -> None:
        items = self.view.selected_items()
        for item in items:
            self.board.remove_image(item)
        if items:
            self.statusBar().showMessage(f"Deleted {len(items)} image(s)", 2000)

    def duplicate_selected(self) -> None:
        clones = [c for c in (self.board.duplicate(i) for i in self.view.selected_items()) if c]
        if clones:
            self.board.clearSelection()
            for clone in clones:
                clone.setSelected(True)

    def select_all(self) -> None:
        for item in self.board.board_items():
            item.setSelected(True)

    def bring_forward(self) -> None:
        for item in self.view.selected_items():
            self.board.bring_to_front(item)

    def send_backward(self) -> None:
        for item in self.view.selected_items():
            self.board.send_to_back(item)

    # ---------- status ----------

    def _update_status(self) -> None:
        total = len(self.board.image_items())
        selected = len(self.view.selected_items())
        zoom = self.view.transform().m11() * 100
        bits = [f"{total} image(s)"]
        if selected:
            bits.append(f"{selected} selected")
        bits.append(f"zoom {zoom:.0f}%")
        self.statusBar().showMessage("   ".join(bits))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    app.setApplicationName("kinesis")

    window = MainWindow()
    window.show()

    # Any image paths / .kinesis file on the command line load at startup.
    for arg in argv[1:]:
        path = Path(arg)
        if not path.exists():
            continue
        if path.suffix == ".kinesis":
            load_scene(window.board, path, window.view)
            window.scene_path = path
        else:
            try:
                window.board.add_image(path)
            except (OSError, ValueError):
                pass
    if len(argv) > 1:
        window.view.zoom_to_fit()

    return app.exec()
