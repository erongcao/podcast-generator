#!/usr/bin/env python3
"""
PySide6 主题 —— 吉卜力配色 + Fusion 样式表

设计原则：
  - 沿用 gui/theme.py 的 GHIBLI dict（同色值），保证两套 UI（如果并存）色调一致
  - 单一 QSS 字符串，app.setStyleSheet() 一次应用；比逐 widget setStyleSheet 干净
  - Fusion 主题作底，QSS 覆盖按钮/输入框圆角和颜色（避免撞 macOS Aqua 默认外观）
"""

# ============== 配色（与 gui/theme.py 同源；改一处即可） ==============

GHIBLI = {
    "sky": "#B8D4E8",
    "sky_dark": "#8BB8D4",
    "forest": "#8DB48E",
    "forest_dark": "#6A9A70",
    "grass": "#A8C4A0",
    "cream": "#F5F0E1",
    "cream_dark": "#E8DCC8",
    "wood": "#8B7355",
    "wood_dark": "#6B5A47",
    "totoro": "#E07B54",
    "totoro_dark": "#C45D3A",
    "dust": "#D4C4A8",
    "text": "#4A4A4A",
    "text_light": "#7A7A7A",
    "white": "#FEFEFE",
    "shadow": "#D0C4A8",
    "error": "#C0392B",
    "ok": "#6A9A70",
}


# ============== 全局 QSS ==============

GHIBLI_QSS = f"""
/* 全局字体 */
* {{
    /* 跨平台字体回退：macOS 用 PingFang SC，中文渲染最稳；Linux/Windows 各自 fallback */
    font-family: "Helvetica Neue", "PingFang SC", "Hiragino Sans GB",
                 "Noto Sans CJK SC", "Microsoft YaHei", sans-serif;
    color: {GHIBLI['text']};
}}

/* 主窗口背景 */
QMainWindow {{
    background-color: {GHIBLI['cream']};
}}

/* GroupBox：圆角、米色背景、森林绿边框 */
QGroupBox {{
    background-color: {GHIBLI['white']};
    border: 2px solid {GHIBLI['forest']};
    border-radius: 10px;
    margin-top: 14px;
    padding: 12px 8px 8px 8px;
    font-weight: bold;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 8px;
    color: {GHIBLI['forest_dark']};
}}

/* 按钮：圆角、森林绿、悬停变深 */
QPushButton {{
    background-color: {GHIBLI['forest']};
    color: {GHIBLI['white']};
    border: none;
    border-radius: 6px;
    padding: 6px 14px;
    font-weight: bold;
    min-height: 22px;
}}
QPushButton:hover {{
    background-color: {GHIBLI['forest_dark']};
}}
QPushButton:pressed {{
    background-color: {GHIBLI['wood_dark']};
}}
QPushButton:disabled {{
    background-color: {GHIBLI['dust']};
    color: {GHIBLI['text_light']};
}}

/* 次要按钮（褐色 outline） */
QPushButton[role="secondary"] {{
    background-color: transparent;
    color: {GHIBLI['wood']};
    border: 1.5px solid {GHIBLI['wood']};
}}
QPushButton[role="secondary"]:hover {{
    background-color: {GHIBLI['cream_dark']};
}}

/* 危险按钮（删除、停止） */
QPushButton[role="danger"] {{
    background-color: {GHIBLI['totoro']};
}}
QPushButton[role="danger"]:hover {{
    background-color: {GHIBLI['totoro_dark']};
}}

/* 输入框：圆角、米色 */
QLineEdit, QPlainTextEdit, QTextEdit {{
    background-color: {GHIBLI['cream']};
    border: 1.5px solid {GHIBLI['dust']};
    border-radius: 6px;
    padding: 4px 6px;
    selection-background-color: {GHIBLI['sky']};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {GHIBLI['sky_dark']};
}}

/* 只读 TextEdit（用于日志） */
QPlainTextEdit[readOnly="true"] {{
    background-color: {GHIBLI['cream_dark']};
    color: {GHIBLI['text']};
    font-family: "Menlo", "Consolas", monospace;
    font-size: 11px;
}}

/* 下拉框 */
QComboBox {{
    background-color: {GHIBLI['cream']};
    border: 1.5px solid {GHIBLI['dust']};
    border-radius: 6px;
    padding: 4px 8px;
    min-height: 22px;
}}
QComboBox:hover {{
    border-color: {GHIBLI['sky_dark']};
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox QAbstractItemView {{
    background-color: {GHIBLI['white']};
    border: 1px solid {GHIBLI['dust']};
    selection-background-color: {GHIBLI['sky']};
}}

/* 进度条：森林绿 */
QProgressBar {{
    background-color: {GHIBLI['cream_dark']};
    border: 1px solid {GHIBLI['dust']};
    border-radius: 6px;
    text-align: center;
    min-height: 18px;
}}
QProgressBar::chunk {{
    background-color: {GHIBLI['forest']};
    border-radius: 5px;
}}

/* Tab Widget：底部导航条样式 */
QTabWidget::pane {{
    border: 1.5px solid {GHIBLI['dust']};
    border-radius: 6px;
    background-color: {GHIBLI['white']};
    margin-top: -1px;
}}
QTabBar::tab {{
    background-color: {GHIBLI['cream_dark']};
    color: {GHIBLI['text_light']};
    padding: 6px 14px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 2px;
}}
QTabBar::tab:selected {{
    background-color: {GHIBLI['white']};
    color: {GHIBLI['forest_dark']};
    border-bottom: 2px solid {GHIBLI['forest']};
}}

/* Checkbox / RadioButton */
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px;
    height: 16px;
}}
QCheckBox::indicator:unchecked {{
    border: 1.5px solid {GHIBLI['dust']};
    background-color: {GHIBLI['white']};
    border-radius: 3px;
}}
QCheckBox::indicator:checked {{
    border: 1.5px solid {GHIBLI['forest_dark']};
    background-color: {GHIBLI['forest']};
    border-radius: 3px;
}}

/* 标签 */
QLabel {{
    color: {GHIBLI['text']};
    background: transparent;
}}
QLabel[role="muted"] {{
    color: {GHIBLI['text_light']};
}}
QLabel[role="title"] {{
    color: {GHIBLI['forest_dark']};
    font-size: 16px;
    font-weight: bold;
}}

/* 列表控件 */
QListWidget {{
    background-color: {GHIBLI['cream']};
    border: 1.5px solid {GHIBLI['dust']};
    border-radius: 6px;
    padding: 4px;
}}
QListWidget::item {{
    padding: 6px 8px;
    border-radius: 4px;
}}
QListWidget::item:selected {{
    background-color: {GHIBLI['sky']};
    color: {GHIBLI['text']};
}}

/* SpinBox / DoubleSpinBox */
QSpinBox, QDoubleSpinBox {{
    background-color: {GHIBLI['cream']};
    border: 1.5px solid {GHIBLI['dust']};
    border-radius: 6px;
    padding: 2px 4px;
}}

/* ScrollBar：米色细条 */
QScrollBar:vertical {{
    background: {GHIBLI['cream']};
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {GHIBLI['dust']};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {GHIBLI['sky_dark']};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: {GHIBLI['cream']};
    height: 10px;
}}
QScrollBar::handle:horizontal {{
    background: {GHIBLI['dust']};
    border-radius: 5px;
    min-width: 30px;
}}

/* ToolTip */
QToolTip {{
    background-color: {GHIBLI['white']};
    color: {GHIBLI['text']};
    border: 1px solid {GHIBLI['dust']};
    border-radius: 4px;
    padding: 4px;
}}
"""
