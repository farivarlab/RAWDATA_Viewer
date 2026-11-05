"""Small pixel-style identity assets for the raw-data desktop application."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget


RESOURCE_DIRECTORY = Path(__file__).with_name("resources")


def application_icon() -> QIcon:
    """Return the scalable RDX/wave icon used by windows and taskbar entries."""
    return QIcon(str(RESOURCE_DIRECTORY / "app-icon.svg"))


def brand_header(parent: QWidget | None = None) -> QWidget:
    """Create a compact classic navy application banner with original artwork."""
    frame = QFrame(parent)
    frame.setObjectName("brandHeader")
    frame.setFixedHeight(46)
    frame.setStyleSheet("""
        QFrame#brandHeader { background: #000080; border-top: 1px solid #ffffff;
            border-left: 1px solid #ffffff; border-right: 1px solid #404040;
            border-bottom: 1px solid #404040; }
        QLabel#brandLogo, QLabel#brandTitle, QLabel#brandSubtitle,
        QLabel#brandWordmark { background: transparent; border: none; color: #ffffff; }
        QLabel#brandSubtitle { color: #c0ffff; }
    """)
    layout = QHBoxLayout(frame)
    layout.setContentsMargins(7, 4, 9, 4)
    layout.setSpacing(8)
    logo = QLabel(frame)
    logo.setObjectName("brandLogo")
    logo.setAccessibleName("Raw Data Explorer logo")
    logo.setPixmap(application_icon().pixmap(32, 32))
    logo.setFixedSize(32, 32)
    layout.addWidget(logo)
    text = QVBoxLayout()
    text.setContentsMargins(0, 0, 0, 0)
    text.setSpacing(0)
    title = QLabel("RAW DATA EXPLORER", frame)
    title.setObjectName("brandTitle")
    title.setTextFormat(Qt.TextFormat.PlainText)
    title.setFont(QFont("Tahoma", 10, QFont.Weight.Bold))
    subtitle = QLabel("Siemens MRI · Twix data workstation", frame)
    subtitle.setObjectName("brandSubtitle")
    subtitle.setTextFormat(Qt.TextFormat.PlainText)
    text.addWidget(title)
    text.addWidget(subtitle)
    layout.addLayout(text)
    layout.addStretch()
    mark = QLabel(frame)
    mark.setObjectName("brandWordmark")
    mark.setAccessibleName("RDX wave wordmark")
    mark.setPixmap(QIcon(str(RESOURCE_DIRECTORY / "wordmark.svg")).pixmap(96, 24))
    mark.setFixedSize(96, 24)
    layout.addWidget(mark)
    return frame
