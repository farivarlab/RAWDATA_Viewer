"""Themed application dialogs and the platform's modern file browser."""

import atexit
from contextlib import contextmanager

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog as _QFileDialog, QMessageBox as _QMessageBox,
)

from .qt_branding import application_icon
from .qt_chrome import ClassicTitleBar, install_native_frame, native_frame_event


_constructing_dialogs = 0


@contextmanager
def _dialog_constructor():
    """Do not decorate temporary base wrappers emitted by Qt constructors."""
    global _constructing_dialogs
    _constructing_dialogs += 1
    try:
        yield
    finally:
        _constructing_dialogs -= 1


def _install_caption(dialog, *, resizable=False, allow_maximize=False):
    if hasattr(dialog, "title_bar"):
        return
    dialog._classic_resizable = resizable
    dialog._classic_allow_minimize = False
    dialog._classic_allow_maximize = allow_maximize
    dialog.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
    dialog.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
    dialog.setWindowIcon(application_icon())
    dialog.setProperty("classicDialog", True)
    dialog.setContentsMargins(3, 3, 3, 3)
    dialog.title_bar = ClassicTitleBar(dialog, allow_minimize=False, allow_maximize=allow_maximize)
    if dialog.layout() is not None:
        # QLayout reserves a menu bar above its contents, even for QGridLayout.
        # This leaves QFileDialog/QMessageBox's own layouts and behavior intact.
        dialog.layout().setMenuBar(dialog.title_bar)
    app = QApplication.instance()
    dialog.title_bar.set_theme(app.property("explorerTheme") if app is not None else "light")


class _PopupFilter(QObject):
    def eventFilter(self, watched, event):
        if (not _constructing_dialogs and event.type() == QEvent.Type.Polish and isinstance(watched, QDialog)
                and not isinstance(watched, _ClassicDialogMixin) and watched.isWindow()):
            # Qt can create its own validation, overwrite, and new-folder prompts
            # from inside a nonnative file chooser. Give those a caption too.
            _install_caption(watched)
        return False


def _ensure_popup_filter():
    app = QApplication.instance()
    if app is not None and not hasattr(app, "_classic_popup_filter"):
        app._classic_popup_filter = _PopupFilter(app)
        app.installEventFilter(app._classic_popup_filter)
        # Remove Python's application-wide callback before PySide tears down
        # C++ widgets at interpreter exit. Child widgets may already be gone.
        def remove_filter():
            from shiboken6 import isValid
            if isValid(app) and isValid(app._classic_popup_filter):
                app.removeEventFilter(app._classic_popup_filter)
        atexit.register(remove_filter)


def _use_native_file_browser(options):
    app = QApplication.instance()
    return (app is not None and app.platformName() not in {"offscreen", "minimal"}
            and not options & _QFileDialog.Option.DontUseNativeDialog)


class _ClassicDialogMixin:
    def showEvent(self, event):
        super().showEvent(event)
        if self.layout() is not None and self.layout().menuBar() is not self.title_bar:
            self.layout().setMenuBar(self.title_bar)
        app = QApplication.instance()
        self.title_bar.set_theme(app.property("explorerTheme") if app is not None else "light")
        install_native_frame(self)

    def nativeEvent(self, event_type, message):
        result = native_frame_event(self, message)
        return result if result is not None else super().nativeEvent(event_type, message)

    def mousePressEvent(self, event):
        if (event.button() == Qt.MouseButton.LeftButton and self._classic_resizable
                and not self.isMaximized() and not self.isFullScreen()):
            point = event.position()
            edges = Qt.Edge(0)
            if point.x() < 5:
                edges |= Qt.Edge.LeftEdge
            elif point.x() >= self.width() - 5:
                edges |= Qt.Edge.RightEdge
            if point.y() < 5:
                edges |= Qt.Edge.TopEdge
            elif point.y() >= self.height() - 5:
                edges |= Qt.Edge.BottomEdge
            if edges and self.windowHandle() is not None and self.windowHandle().startSystemResize(edges):
                event.accept()
                return
        super().mousePressEvent(event)


class QMessageBox(_ClassicDialogMixin, _QMessageBox):
    def __init__(self, *args, **kwargs):
        _ensure_popup_filter()
        with _dialog_constructor():
            super().__init__(*args, **kwargs)
        _install_caption(self)

    @classmethod
    def _message(cls, icon, parent, title, text, buttons, defaultButton):
        dialog = cls(parent)
        dialog.setWindowTitle(title)
        dialog.setIcon(icon)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(text)
        dialog.setStandardButtons(buttons)
        if defaultButton != cls.StandardButton.NoButton:
            dialog.setDefaultButton(defaultButton)
        try:
            return cls.StandardButton(dialog.exec())
        finally:
            dialog.deleteLater()

    @classmethod
    def information(cls, parent, title, text, buttons=_QMessageBox.StandardButton.Ok,
                    defaultButton=_QMessageBox.StandardButton.NoButton):
        return cls._message(cls.Icon.Information, parent, title, text, buttons, defaultButton)

    @classmethod
    def warning(cls, parent, title, text, buttons=_QMessageBox.StandardButton.Ok,
                defaultButton=_QMessageBox.StandardButton.NoButton):
        return cls._message(cls.Icon.Warning, parent, title, text, buttons, defaultButton)

    @classmethod
    def critical(cls, parent, title, text, buttons=_QMessageBox.StandardButton.Ok,
                 defaultButton=_QMessageBox.StandardButton.NoButton):
        return cls._message(cls.Icon.Critical, parent, title, text, buttons, defaultButton)

    @classmethod
    def question(cls, parent, title, text,
                 buttons=_QMessageBox.StandardButton.Yes | _QMessageBox.StandardButton.No,
                 defaultButton=_QMessageBox.StandardButton.NoButton):
        return cls._message(cls.Icon.Question, parent, title, text, buttons, defaultButton)


class QFileDialog(_ClassicDialogMixin, _QFileDialog):
    def __init__(self, *args, **kwargs):
        _ensure_popup_filter()
        with _dialog_constructor():
            super().__init__(*args, **kwargs)
        self.setOption(self.Option.DontUseNativeDialog, True)
        _install_caption(self, resizable=True, allow_maximize=True)

    @classmethod
    def _file_name(cls, parent, caption, directory, filter, selectedFilter, options, *, save):
        if _use_native_file_browser(options):
            choose = _QFileDialog.getSaveFileName if save else _QFileDialog.getOpenFileName
            # Let the OS own the browser and its child prompts. Decorating a
            # temporary Qt wrapper would prevent the native picker from opening.
            with _dialog_constructor():
                return choose(parent, caption, directory, filter, selectedFilter, options)
        dialog = cls(parent, caption, directory, filter)
        dialog.setOptions(options | cls.Option.DontUseNativeDialog)
        dialog.setAcceptMode(cls.AcceptMode.AcceptSave if save else cls.AcceptMode.AcceptOpen)
        dialog.setFileMode(cls.FileMode.AnyFile if save else cls.FileMode.ExistingFile)
        if selectedFilter:
            dialog.selectNameFilter(selectedFilter)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return "", ""
            files = dialog.selectedFiles()
            return (files[0], dialog.selectedNameFilter()) if files else ("", "")
        finally:
            dialog.deleteLater()

    @classmethod
    def getOpenFileName(cls, parent=None, caption="", dir="", filter="", selectedFilter="",
                        options=_QFileDialog.Option(0)):
        return cls._file_name(parent, caption, dir, filter, selectedFilter, options, save=False)

    @classmethod
    def getSaveFileName(cls, parent=None, caption="", dir="", filter="", selectedFilter="",
                        options=_QFileDialog.Option(0)):
        return cls._file_name(parent, caption, dir, filter, selectedFilter, options, save=True)
