# -*- coding: utf-8 -*-
"""黑夜模式主题 —— 「端口画板」与主程序共用的**唯一**一份深色配色。

为什么单独成模块：端口画板既要嵌在 main.py 里用，也要能被单独打包成 exe 给别的用户用。
嵌在 main.py 里时它继承主窗口的深色调色板；单独跑时没有任何父窗口，
**会跟随系统明暗**（在浅色 Windows 上就是一片白）。把配色抽到这里之后，
两条路径用的是逐字相同的调色板与样式表，也就不会出现"两处各异"。

用法：
    from dark_theme import build_dark_palette, DARK_STYLESHEET, apply_dark_theme
    apply_dark_theme(dialog)                  # 固定某个窗口
    QApplication.setPalette(build_dark_palette())   # 全局
"""
from PySide6.QtGui import QColor, QPalette


def build_dark_palette():
    """构造深色调色板（与主程序 `set_dark_theme` 逐字一致）。"""
    dark_palette = QPalette()
    dark_palette.setColor(QPalette.Window, QColor(45, 45, 45))
    dark_palette.setColor(QPalette.WindowText, QColor(255, 255, 255))
    dark_palette.setColor(QPalette.Base, QColor(35, 35, 35))
    dark_palette.setColor(QPalette.AlternateBase, QColor(45, 45, 45))
    # Tooltip：深色底 + 白色字（修复原白底白字不可读问题）
    dark_palette.setColor(QPalette.ToolTipBase, QColor(45, 45, 48))
    dark_palette.setColor(QPalette.ToolTipText, QColor(255, 255, 255))
    dark_palette.setColor(QPalette.Text, QColor(255, 255, 255))
    dark_palette.setColor(QPalette.Button, QColor(65, 65, 65))
    dark_palette.setColor(QPalette.ButtonText, QColor(255, 255, 255))
    dark_palette.setColor(QPalette.BrightText, QColor(255, 0, 0))
    dark_palette.setColor(QPalette.Link, QColor(42, 130, 218))
    dark_palette.setColor(QPalette.Highlight, QColor(42, 130, 218))
    dark_palette.setColor(QPalette.HighlightedText, QColor(0, 0, 0))

    # 禁用颜色
    dark_palette.setColor(QPalette.Disabled, QPalette.WindowText, QColor(127, 127, 127))
    dark_palette.setColor(QPalette.Disabled, QPalette.Text, QColor(127, 127, 127))
    dark_palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(127, 127, 127))
    return dark_palette


DARK_STYLESHEET = """
QMainWindow, QDialog, QWidget {
    background-color: #2d2d2d;
    color: #ffffff;
    border: none;
}

QLabel {
    color: #ffffff;
    background-color: transparent;
}

QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background-color: #3d3d3d;
    color: #ffffff;
    border: 1px solid #555555;
    border-radius: 3px;
    padding: 3px;
}

QPushButton {
    background-color: #4a4a4a;
    color: #ffffff;
    border: 1px solid #555555;
    border-radius: 3px;
    padding: 5px;
}

QPushButton:hover {
    background-color: #5a5a5a;
}

QPushButton:pressed {
    background-color: #3a3a3a;
}

QPushButton:disabled {
    background-color: #353535;
    color: #7f7f7f;
}

QListWidget, QTreeWidget, QTableView {
    background-color: #3d3d3d;
    color: #ffffff;
    border: 1px solid #555555;
    border-radius: 3px;
    gridline-color: #555555;
    outline: 0;
}

QListWidget::item:selected, QTreeWidget::item:selected, QTableView::item:selected {
    background-color: #2a82da;
    color: #000000;
}

QListWidget::item:hover, QTreeWidget::item:hover, QTableView::item:hover {
    background-color: #4a4a4a;
}

QHeaderView::section {
    background-color: #4a4a4a;
    color: #ffffff;
    padding: 4px;
    border: 1px solid #555555;
}

QTabWidget::pane {
    border: 1px solid #555555;
    background: #3d3d3d;
}

QTabBar::tab {
    background: #4a4a4a;
    color: #ffffff;
    padding: 8px;
    border: 1px solid #555555;
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}

QTabBar::tab:selected {
    background: #3d3d3d;
    border-bottom: 2px solid #2a82da;
}

QTabBar::tab:hover:!selected {
    background: #5a5a5a;
}

QMenuBar {
    background-color: #3d3d3d;
    color: #ffffff;
}

QMenuBar::item {
    background: transparent;
}

QMenuBar::item:selected {
    background: #5a5a5a;
}

QMenu {
    background-color: #3d3d3d;
    color: #ffffff;
    border: 1px solid #555555;
}

QMenu::item:selected {
    background-color: #2a82da;
    color: #000000;
}

QScrollBar:vertical {
    border: none;
    background: #3d3d3d;
    width: 10px;
    margin: 0px;
}

QScrollBar::handle:vertical {
    background: #5a5a5a;
    min-height: 20px;
    border-radius: 5px;
}

QScrollBar::handle:vertical:hover {
    background: #6a6a6a;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

QScrollBar:horizontal {
    border: none;
    background: #3d3d3d;
    height: 10px;
    margin: 0px;
}

QScrollBar::handle:horizontal {
    background: #5a5a5a;
    min-width: 20px;
    border-radius: 5px;
}

QScrollBar::handle:horizontal:hover {
    background: #6a6a6a;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}

QProgressBar {
    border: 1px solid #555555;
    border-radius: 3px;
    text-align: center;
    background-color: #3d3d3d;
}

QProgressBar::chunk {
    background-color: #2a82da;
    width: 10px;
}

QGroupBox {
    font-weight: bold;
    border: 1px solid #555555;
    border-radius: 5px;
    margin-top: 10px;
    padding-top: 10px;
    background-color: #3d3d3d;
    color: #ffffff;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top center;
    padding: 0 5px;
}

QStatusBar {
    background-color: #3d3d3d;
    color: #ffffff;
}

QToolTip {
    color: #ffffff;
    background-color: #2d2d30;
    border: 1px solid #555555;
    padding: 4px;
}

QToolBar {
    background-color: #3d3d3d;
    border: none;
}

QSplitter::handle {
    background-color: #555555;
}

QSplitter::handle:hover {
    background-color: #2a82da;
}

QCheckBox, QRadioButton {
    color: #ffffff;
}

QCheckBox::indicator, QRadioButton::indicator {
    border: 1px solid #555555;
    background: #3d3d3d;
}

QCheckBox::indicator:checked, QRadioButton::indicator:checked {
    background: #2a82da;
    border: 1px solid #2a82da;
}

QSlider::groove:horizontal {
    border: 1px solid #555555;
    height: 8px;
    background: #3d3d3d;
    border-radius: 4px;
}

QSlider::handle:horizontal {
    background: #2a82da;
    border: 1px solid #555555;
    width: 18px;
    margin: -5px 0;
    border-radius: 9px;
}

QSlider::groove:vertical {
    border: 1px solid #555555;
    width: 8px;
    background: #3d3d3d;
    border-radius: 4px;
}

QSlider::handle:vertical {
    background: #2a82da;
    border: 1px solid #555555;
    height: 18px;
    margin: 0 -5px;
    border-radius: 9px;
}
"""


def apply_dark_theme(widget):
    """把一个窗口（及其全部子控件）**固定**成黑夜模式，不跟随系统明暗。

    只作用于这个 widget，不动 QApplication 的全局调色板 —— 这样端口画板
    在被嵌进主程序时不会把主窗口已经设好的主题再改一遍。
    """
    if widget is None:
        return
    widget.setPalette(build_dark_palette())
    widget.setStyleSheet(DARK_STYLESHEET)
    return widget
