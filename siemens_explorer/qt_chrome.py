"""Compact classic window controls with native Windows dragging and resizing."""

from functools import lru_cache
import sys

from PySide6.QtCore import QEvent, QPoint, QSize, Qt
from PySide6.QtGui import QAction, QColor, QPainter, QPalette
from PySide6.QtWidgets import QApplication, QDockWidget, QHBoxLayout, QLabel, QMenu, QToolButton, QWidget

from .qt_glyphs import glyph_icon
from .qt_theme import load_theme, MODERN_THEMES


def frame_hit_test(x, y, width, height, margin=5):
    """Windows resize hit codes, in one consistent (physical-pixel) space."""
    if not (0 <= x < width and 0 <= y < height):
        return None
    left, right = x < margin, x >= width - margin
    top, bottom = y < margin, y >= height - margin
    if top:
        return 13 if left else 14 if right else 12  # top-left / top-right / top
    if bottom:
        return 16 if left else 17 if right else 15
    return 10 if left else 11 if right else None


def _caption_icon(kind, color="#000000", background="#c0c0c0"):
    return glyph_icon(kind, color=color, background=background)


class _TitleLabel(QLabel):
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(self.palette().windowText().color())
        painter.setFont(self.font())
        area = self.contentsRect()
        painter.drawText(area, Qt.AlignmentFlag.AlignVCenter,
                         self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, area.width()))


class ClassicTitleBar(QWidget):
    """A real replacement caption, rather than an extra banner in the workspace."""

    def __init__(self, window, *, allow_minimize=True, allow_maximize=True):
        super().__init__(window)
        self.window = window
        self.allow_minimize = allow_minimize
        self.allow_maximize = allow_maximize
        self._icon_color = "#000000"
        self._button_background = "#c0c0c0"
        self._drag_offset = None
        self.setObjectName("classicTitleBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(28)
        self.setProperty("windowActive", False)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 2, 3, 2)
        layout.setSpacing(3)
        self.system_button = QToolButton()
        self.system_button.setObjectName("windowSystemIcon")
        self.system_button.setFixedSize(24, 22)
        self.system_button.setIcon(window.windowIcon())
        self.system_button.setAccessibleName("Window menu")
        self.system_button.clicked.connect(self.open_system_menu)
        layout.addWidget(self.system_button)
        self.title = _TitleLabel(window.windowTitle())
        self.title.setObjectName("classicWindowTitle")
        self.title.setMinimumWidth(0)
        from PySide6.QtWidgets import QSizePolicy
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.title, 1)
        self.minimize_button = self._button("Minimize window", "minimize", window.showMinimized)
        self.maximize_button = self._button("Maximize window", "maximize", self.toggle_maximized)
        self.close_button = self._button("Close window", "close", window.close)
        self.close_button.setObjectName("closeWindowButton")
        for button in (self.minimize_button, self.maximize_button, self.close_button):
            layout.addWidget(button)
        self.minimize_button.setVisible(allow_minimize)
        self.maximize_button.setVisible(allow_maximize)
        window.windowTitleChanged.connect(self._set_title)
        window.windowIconChanged.connect(self.system_button.setIcon)
        window.installEventFilter(self)
        self.system_menu_action = QAction("Window menu", window)
        self.system_menu_action.setShortcut("Alt+Space")
        self.system_menu_action.triggered.connect(self.open_system_menu)
        window.addAction(self.system_menu_action)
        self._set_title(window.windowTitle())
        app = QApplication.instance()
        self.set_theme(app.property("explorerTheme") if app is not None else "light")
        self._sync_state()

    def _button(self, name, kind, callback):
        button = QToolButton()
        button.setProperty("captionButton", True)
        button.setFixedSize(26, 22)
        button.setIcon(_caption_icon(kind))
        button.setAccessibleName(name)
        button.setToolTip(name)
        button.clicked.connect(callback)
        return button

    def _set_title(self, text):
        self.title.setText(text)
        self.title.setToolTip(text)

    def set_theme(self, name, assets=None):
        """Follow application theme changes without altering window controls."""
        assets = assets or load_theme(name or "cockpit")
        cockpit = assets.name in MODERN_THEMES
        self.setFixedHeight(32 if cockpit else 28)
        self.layout().setContentsMargins(4 if cockpit else 3, 2, 2 if cockpit else 3, 2)
        self.layout().setSpacing(0 if cockpit else 3)
        self.system_button.setFixedSize(24, 28 if cockpit else 22)
        self.title.setContentsMargins(5 if cockpit else 0, 0, 8 if cockpit else 0, 0)
        for button in (self.minimize_button, self.maximize_button, self.close_button):
            button.setFixedSize(36 if cockpit else 26, 28 if cockpit else 22)
            button.setIconSize(QSize(16, 16))
        self._icon_color = assets.colors["caption_text"]
        self._button_background = assets.colors["caption_button"]
        self.setStyleSheet(assets.caption_stylesheet)
        self.minimize_button.setIcon(_caption_icon("minimize", self._icon_color, self._button_background))
        self.close_button.setIcon(_caption_icon("close", self._icon_color, self._button_background))
        self._sync_state()

    def _sync_state(self):
        active = self.window.isActiveWindow()
        if self.property("windowActive") != active:
            self.setProperty("windowActive", active)
            self.style().unpolish(self)
            self.style().polish(self)
        maximized = self.window.isMaximized()
        self.maximize_button.setIcon(_caption_icon("restore" if maximized else "maximize",
                                                    self._icon_color, self._button_background))
        self.maximize_button.setToolTip("Restore window" if maximized else "Maximize window")
        self.maximize_button.setAccessibleName(self.maximize_button.toolTip())
        self.setVisible(not self.window.isFullScreen())

    def eventFilter(self, watched, event):
        from shiboken6 import isValid
        if isValid(self.window) and watched is self.window and event.type() == QEvent.Type.WindowIconChange:
            self.system_button.setIcon(self.window.windowIcon())
        if (isValid(self.window) and watched is self.window
                and event.type() in (QEvent.Type.WindowStateChange, QEvent.Type.ActivationChange)):
            self._sync_state()
        return False

    def toggle_maximized(self):
        if not self.allow_maximize:
            return
        if self.window.isMaximized() or self.window.isFullScreen():
            self.window.showNormal()
        else:
            self.window.showMaximized()
        self._sync_state()

    def open_system_menu(self):
        menu = QMenu(self)
        if self.allow_maximize or self.allow_minimize:
            restore = menu.addAction("Restore", self.window.showNormal)
            restore.setEnabled(self.window.isMaximized() or self.window.isMinimized() or self.window.isFullScreen())
            if self.allow_minimize:
                menu.addAction("Minimize", self.window.showMinimized)
            if self.allow_maximize:
                maximize = menu.addAction("Maximize", self.window.showMaximized)
                maximize.setEnabled(not self.window.isMaximized())
            menu.addSeparator()
        menu.addAction("Close\tAlt+F4", self.window.close)
        menu.exec(self.system_button.mapToGlobal(QPoint(0, self.system_button.height())))

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_maximized()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window.windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
            # Fallback for platforms without Qt system-move support.
            if not self.window.isMaximized() and not self.window.isFullScreen():
                self._drag_offset = event.globalPosition().toPoint() - self.window.pos()
            event.accept()
        elif event.button() == Qt.MouseButton.RightButton:
            self.open_system_menu()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class _DockTitleBar(ClassicTitleBar):
    """Leave docking gestures to Qt instead of starting a native window drag."""

    def _sync_state(self):
        super()._sync_state()
        self.setVisible(self.window.isFloating())

    def open_system_menu(self):
        menu = QMenu(self)
        menu.addAction("Dock", lambda: self.window.setFloating(False))
        menu.addSeparator()
        menu.addAction("Hide", self.window.close)
        menu.exec(self.system_button.mapToGlobal(QPoint(0, self.system_button.height())))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.open_system_menu()
        else:
            event.ignore()

    def mouseMoveEvent(self, event):
        event.ignore()

    def mouseReleaseEvent(self, event):
        event.ignore()

    def mouseDoubleClickEvent(self, event):
        # Qt's dock-widget behavior docks a floating panel on double-click.
        event.ignore()


class _DockOutline(QWidget):
    """Paint above dock contents; Qt can omit QSS frames on floating docks."""

    def __init__(self, dock):
        super().__init__(dock)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def paintEvent(self, event):
        app = QApplication.instance()
        colors = app.property("explorerThemeColors") or {}
        color = QColor(colors.get("border", self.palette().color(QPalette.ColorRole.Mid).name()))
        painter = QPainter(self)
        painter.setPen(color)
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))


class ClassicDockWidget(QDockWidget):
    """A normal dock while attached, with a matching caption when floating."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._classic_resizable = True
        self._classic_allow_minimize = False
        self._classic_allow_maximize = False
        self.floating_title_bar = _DockTitleBar(self, allow_minimize=False, allow_maximize=False)
        self.floating_title_bar.setToolTip("Drag to move or dock; double-click to dock.")
        self.floating_title_bar.system_menu_action.setEnabled(False)
        self._outline = _DockOutline(self)
        self._sync_outline()
        self.topLevelChanged.connect(self._floating_changed)

    def _sync_outline(self):
        if hasattr(self, "_outline"):
            self._outline.setGeometry(self.rect())
            self._outline.show()
            self._outline.raise_()
            self._outline.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_outline()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.StyleChange):
            self._sync_outline()

    def _floating_changed(self, floating):
        self._classic_native_frame = False
        self.floating_title_bar.system_menu_action.setEnabled(floating)
        self.setTitleBarWidget(self.floating_title_bar if floating else None)
        self.floating_title_bar.setVisible(floating)
        if floating:
            parent = self.parentWidget()
            if parent is not None:
                self.setWindowIcon(parent.windowIcon())
            app = QApplication.instance()
            self.floating_title_bar.set_theme(app.property("explorerTheme") if app is not None else "light")
            install_native_frame(self)
        self._sync_outline()

    def showEvent(self, event):
        super().showEvent(event)
        if self.isFloating() and hasattr(self, "floating_title_bar"):
            install_native_frame(self)
        self._sync_outline()

    def nativeEvent(self, event_type, message):
        if self.isFloating():
            result = native_frame_event(self, message)
            if result is not None:
                return result
        return super().nativeEvent(event_type, message)


@lru_cache(maxsize=1)
def _windows_api():
    import ctypes
    from ctypes import wintypes

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    class MinMaxInfo(ctypes.Structure):
        _fields_ = [(name, wintypes.POINT) for name in
                    ("ptReserved", "ptMaxSize", "ptMaxPosition", "ptMinTrackSize", "ptMaxTrackSize")]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    get_style.argtypes, get_style.restype = [wintypes.HWND, ctypes.c_int], ctypes.c_ssize_t
    set_style.argtypes, set_style.restype = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t], ctypes.c_ssize_t
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, wintypes.UINT]
    return ctypes, wintypes, user32, get_style, set_style, MonitorInfo, MinMaxInfo


def _native_windows():
    app = QApplication.instance()
    return sys.platform == "win32" and app is not None and app.platformName() == "windows"


def install_native_frame(window):
    """Retain Windows sizing/Snap styles while drawing our own square frame."""
    if not _native_windows() or getattr(window, "_classic_native_frame", False):
        return
    ctypes, wintypes, user32, get_style, set_style, _, _ = _windows_api()
    hwnd = int(window.winId())
    style = get_style(hwnd, -16)
    style |= 0x00080000  # WS_SYSMENU
    for flag, allowed in ((0x00040000, getattr(window, "_classic_resizable", True)),
                          (0x00020000, getattr(window, "_classic_allow_minimize", True)),
                          (0x00010000, getattr(window, "_classic_allow_maximize", True))):
        style = style | flag if allowed else style & ~flag
    set_style(hwnd, -16, style)
    window._classic_native_frame = True
    user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0027)  # frame changed; retain size/position/z-order
    try:
        dwm = ctypes.WinDLL("dwmapi")
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        square_corners = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(square_corners), ctypes.sizeof(square_corners))
    except OSError:
        pass


def native_frame_event(window, message):
    """Handle sizing edges and maximized work area for this application's HWND."""
    if not _native_windows():
        return None
    ctypes, wintypes, user32, _, _, MonitorInfo, MinMaxInfo = _windows_api()
    msg = wintypes.MSG.from_address(int(message))
    if msg.message == 0x0083:  # WM_NCCALCSIZE: omit the system caption/frame
        return True, 0
    if (msg.message == 0x0084 and getattr(window, "_classic_resizable", True)
            and not window.isMaximized() and not window.isFullScreen()):
        rect = wintypes.RECT()
        if user32.GetWindowRect(msg.hWnd, ctypes.byref(rect)):
            x = ctypes.c_short(msg.lParam & 0xFFFF).value - rect.left
            y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value - rect.top
            edge = frame_hit_test(x, y, rect.right - rect.left, rect.bottom - rect.top,
                                  max(4, round(4 * window.devicePixelRatioF())))
            if edge is not None:
                return True, edge
    if msg.message == 0x0024:  # WM_GETMINMAXINFO: keep the Windows taskbar visible
        monitor = user32.MonitorFromWindow(msg.hWnd, 2)
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            limits = MinMaxInfo.from_address(msg.lParam)
            limits.ptMaxPosition.x = info.rcWork.left - info.rcMonitor.left
            limits.ptMaxPosition.y = info.rcWork.top - info.rcMonitor.top
            limits.ptMaxSize.x = info.rcWork.right - info.rcWork.left
            limits.ptMaxSize.y = info.rcWork.bottom - info.rcWork.top
            limits.ptMinTrackSize.x = round(window.minimumWidth() * window.devicePixelRatioF())
            limits.ptMinTrackSize.y = round(window.minimumHeight() * window.devicePixelRatioF())
            return True, 0
    return None
