"""One stylesheet for the whole application.

Deliberately flat and static: requirement 18 asks for speed and clarity and no
unnecessary animation, and office machines are often remote-desktop sessions
where gradients and shadows cost more than they give.
"""
from __future__ import annotations

NAVY = "#16385a"
NAVY_DARK = "#0f2942"
INK = "#101a24"
INK_2 = "#4a5867"
INK_3 = "#7d8a99"
RULE = "#dde3ea"
PLANE = "#f4f6f9"
SURFACE = "#ffffff"
GOOD = "#0f8a34"
GOOD_WASH = "#eaf7ea"
WARN = "#8a5d00"
WARN_WASH = "#fdf5e3"
BAD = "#b3261e"
BAD_WASH = "#fceceb"
ACCENT = "#2a78d6"

STYLESHEET = f"""
QWidget {{
    background: {PLANE};
    color: {INK};
    font-family: "Segoe UI", "Inter", system-ui, sans-serif;
    font-size: 14px;
}}
QMainWindow, QDialog {{ background: {PLANE}; }}
/* Labels must not paint the page colour over the surface they sit on; the pill
   rules below re-declare a background for the chips that need one. */
QLabel {{ background: transparent; }}
QCheckBox {{ background: transparent; }}

/* ---------- header ---------- */
#Header {{ background: {NAVY_DARK}; }}
#HeaderTitle {{ color: #ffffff; font-size: 16px; font-weight: 700; letter-spacing: 1px; }}
#HeaderSub  {{ color: rgba(255,255,255,0.62); font-size: 12px; }}
#HeaderBadge {{
    color: #dbe7f4; background: {NAVY}; border-radius: 10px;
    padding: 3px 9px; font-size: 11px; font-family: Consolas, monospace;
}}

/* ---------- cards ---------- */
#Card {{ background: {SURFACE}; border: 1px solid {RULE}; border-radius: 10px; }}
#CardTitle {{ font-size: 12px; font-weight: 700; color: {INK_3}; letter-spacing: 1px; }}
#SectionTitle {{ font-size: 15px; font-weight: 700; }}
#Hint {{ color: {INK_3}; font-size: 12px; }}
#Muted {{ color: {INK_2}; }}

/* ---------- status pills ---------- */
QLabel[pill="good"] {{
    background: {GOOD_WASH}; color: {GOOD}; border-radius: 10px;
    padding: 3px 10px; font-weight: 600;
}}
QLabel[pill="warn"] {{
    background: {WARN_WASH}; color: {WARN}; border-radius: 10px;
    padding: 3px 10px; font-weight: 600;
}}
QLabel[pill="bad"] {{
    background: {BAD_WASH}; color: {BAD}; border-radius: 10px;
    padding: 3px 10px; font-weight: 600;
}}
QLabel[pill="idle"] {{
    background: #eef2f6; color: {INK_2}; border-radius: 10px;
    padding: 3px 10px; font-weight: 600;
}}

/* ---------- metrics ---------- */
#MetricValue {{ font-size: 30px; font-weight: 700; }}
#MetricLabel {{ font-size: 11px; font-weight: 700; color: {INK_3}; letter-spacing: 1px; }}
#Timestamp   {{ font-size: 15px; font-weight: 600; }}

/* ---------- buttons ---------- */
QPushButton {{
    background: {SURFACE}; color: {INK}; border: 1px solid {RULE};
    border-radius: 7px; padding: 8px 14px; font-size: 13px; font-weight: 600;
}}
QPushButton:hover  {{ background: #fafbfd; border-color: #c8d2dc; }}
QPushButton:pressed {{ background: #f0f3f7; }}
QPushButton:disabled {{ color: #a9b3bd; border-color: #e9eef3; background: #fbfcfd; }}

QPushButton#Primary {{
    background: {NAVY}; color: #ffffff; border: 1px solid {NAVY};
}}
QPushButton#Primary:hover   {{ background: #1d4570; border-color: #1d4570; }}
QPushButton#Primary:pressed {{ background: {NAVY_DARK}; }}
QPushButton#Primary:disabled {{ background: #93a6ba; border-color: #93a6ba; color: #eef2f6; }}

QPushButton#Refresh {{
    background: {NAVY}; color: #ffffff; border: 1px solid {NAVY};
    border-radius: 9px; padding: 18px 24px; font-size: 16px; font-weight: 700;
    letter-spacing: 0.5px;
}}
QPushButton#Refresh:hover    {{ background: #1d4570; }}
QPushButton#Refresh:pressed  {{ background: {NAVY_DARK}; }}
QPushButton#Refresh:disabled {{ background: #93a6ba; border-color: #93a6ba; color: #f2f5f8; }}

QPushButton#Danger {{ color: {BAD}; border-color: #e7c6c3; }}
QPushButton#Danger:hover {{ background: {BAD_WASH}; }}

/* ---------- bottom navigation ---------- */
#NavBar {{ background: {NAVY}; }}
QPushButton#NavButton {{
    background: transparent; color: #c6d6e6; border: none; border-radius: 6px;
    padding: 9px 16px; font-weight: 600;
}}
QPushButton#NavButton:hover {{ background: rgba(255,255,255,0.09); color: #ffffff; }}
QPushButton#NavButton:checked {{ background: {SURFACE}; color: {NAVY_DARK}; }}

/* ---------- inputs ---------- */
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit {{
    background: {SURFACE}; border: 1px solid {RULE}; border-radius: 7px;
    padding: 7px 9px; selection-background-color: #cfe0f5; selection-color: {INK};
}}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QLineEdit:disabled, QComboBox:disabled {{ background: #f7f9fb; color: {INK_3}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE}; border: 1px solid {RULE}; selection-background-color: #eaf1fb;
    selection-color: {INK}; outline: none;
}}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border: 1px solid #c8d2dc; border-radius: 4px; background: white;
}}
QCheckBox::indicator:checked {{ background: {NAVY}; border-color: {NAVY}; }}

/* ---------- tables ---------- */
QTableWidget, QTreeWidget, QListWidget {{
    background: {SURFACE}; border: 1px solid {RULE}; border-radius: 8px;
    gridline-color: #eef2f6; outline: none;
}}
QTableWidget::item, QTreeWidget::item {{ padding: 5px 6px; }}
QTableWidget::item:selected, QTreeWidget::item:selected,
QListWidget::item:selected {{ background: #eaf1fb; color: {INK}; }}
QHeaderView::section {{
    background: #fafbfd; color: {INK_2}; border: none; border-bottom: 1px solid {RULE};
    border-right: 1px solid #eef2f6; padding: 7px 8px; font-size: 11px; font-weight: 700;
    letter-spacing: 0.6px;
}}
QTableCornerButton::section {{ background: #fafbfd; border: none; }}

/* ---------- misc ---------- */
QPlainTextEdit#LogView {{
    font-family: Consolas, "Cascadia Mono", monospace; font-size: 12px;
    background: #fbfcfd;
}}
QProgressBar {{
    background: #eef2f6; border: none; border-radius: 4px; height: 6px; text-align: center;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
QScrollArea {{ border: none; background: {PLANE}; }}
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #c8d2dc; border-radius: 5px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: #a9b7c5; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: #c8d2dc; border-radius: 5px; min-width: 28px; }}
QToolTip {{
    background: {NAVY_DARK}; color: #ffffff; border: none; padding: 6px 8px; border-radius: 5px;
}}
QGroupBox {{
    border: 1px solid {RULE}; border-radius: 9px; margin-top: 16px; padding: 12px;
    background: {SURFACE}; font-weight: 700;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 5px; color: {INK_2}; }}
"""


def pill_for_state(state: str) -> str:
    """Map a connection state onto a pill style."""
    from services.auth_service import ConnState
    return {
        ConnState.CONNECTED: "good",
        ConnState.EXPIRED: "warn",
        ConnState.ERROR: "bad",
        ConnState.NOT_CONNECTED: "idle",
    }.get(state, "idle")
