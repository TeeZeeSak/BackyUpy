"""Application styling.

A single dark theme keeps the dashboard readable for long review sessions and
matches the mock-up in the specification. Colours are also exposed as plain
constants so non-Qt renderers (e.g. the HTML report) can reuse them.
"""

from __future__ import annotations

# Bucket colours.
CRITICAL_COLOR = "#ff6b6b"
IMPORTANT_COLOR = "#ffb454"
REVIEW_COLOR = "#6fb3ff"
IGNORE_COLOR = "#8a8f98"

BACKGROUND = "#1e1f22"
SURFACE = "#26282c"
SURFACE_ALT = "#2c2f34"
BORDER = "#3a3d41"
TEXT = "#e6e6e6"
TEXT_MUTED = "#9aa0a6"
ACCENT = "#6fb3ff"

DARK_STYLESHEET = f"""
QWidget {{
    background-color: {BACKGROUND};
    color: {TEXT};
    font-family: 'Segoe UI', system-ui, sans-serif;
    font-size: 13px;
}}
QMainWindow, QDialog {{ background-color: {BACKGROUND}; }}

QLabel#Title {{ font-size: 20px; font-weight: 700; letter-spacing: 1px; }}
QLabel#Subtitle {{ color: {TEXT_MUTED}; }}
QLabel#BadgeLocal {{
    background-color: #1f3d2b; color: #7ee2a8; border: 1px solid #2f6b47;
    border-radius: 10px; padding: 2px 10px; font-weight: 600;
}}
QLabel#BadgeRemote {{
    background-color: #4a2a2a; color: #ff9d9d; border: 1px solid #7a3b3b;
    border-radius: 10px; padding: 2px 10px; font-weight: 600;
}}
QLabel#SectionHeader {{ font-size: 14px; font-weight: 700; color: {TEXT_MUTED}; }}

QFrame#Card {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QLabel#CardValue {{ font-size: 22px; font-weight: 700; }}
QLabel#CardLabel {{ color: {TEXT_MUTED}; }}

QTableWidget {{
    background-color: {SURFACE};
    alternate-background-color: {SURFACE_ALT};
    gridline-color: {BORDER};
    border: 1px solid {BORDER};
    border-radius: 8px;
    selection-background-color: #34455c;
}}
QHeaderView::section {{
    background-color: {SURFACE_ALT};
    color: {TEXT_MUTED};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 6px;
    font-weight: 600;
}}
QTableWidget::item {{ padding: 4px; }}

QPushButton {{
    background-color: {SURFACE_ALT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 7px 14px;
}}
QPushButton:hover {{ background-color: #34383d; }}
QPushButton:disabled {{ color: #5a5f66; }}
QPushButton#Primary {{
    background-color: #2f6b47; border-color: #3f8a5e; font-weight: 600;
}}
QPushButton#Primary:hover {{ background-color: #377f55; }}
QPushButton#Danger {{ background-color: #6b2f2f; border-color: #8a3f3f; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px 8px;
}}
QComboBox QAbstractItemView {{
    background-color: {SURFACE}; selection-background-color: #34455c;
}}
QCheckBox {{ spacing: 6px; }}
QProgressBar {{
    background-color: {SURFACE}; border: 1px solid {BORDER};
    border-radius: 6px; text-align: center; height: 18px;
}}
QProgressBar::chunk {{ background-color: {ACCENT}; border-radius: 5px; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 6px; }}
QToolTip {{
    background-color: {SURFACE_ALT}; color: {TEXT}; border: 1px solid {BORDER};
}}
QScrollBar:vertical {{ background: {BACKGROUND}; width: 12px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #454a51; border-radius: 6px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""


def bucket_color(bucket: str) -> str:
    """Return the theme colour for a bucket name."""
    return {
        "CRITICAL": CRITICAL_COLOR,
        "IMPORTANT": IMPORTANT_COLOR,
        "REVIEW": REVIEW_COLOR,
        "IGNORE": IGNORE_COLOR,
    }.get(bucket.upper(), IGNORE_COLOR)
