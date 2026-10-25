from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QLibraryInfo,
    QProcess,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTranslator,
    QUrl,
)
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QFontDatabase,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizeGrip,
    QSizePolicy,
    QStackedLayout,
    QStyle,
    QStyleFactory,
    QVBoxLayout,
    QWidget,
)


class CheckBox(QCheckBox):
    INDICATOR_SIZE = 22
    SPACING = 10

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self) -> QSize:
        text = self.fontMetrics().size(Qt.TextFlag.TextShowMnemonic, self.text())
        width = self.INDICATOR_SIZE + 2
        if self.text():
            width += self.SPACING + text.width()
        return QSize(width, max(self.INDICATOR_SIZE + 2, text.height()))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = self.checkState() != Qt.CheckState.Unchecked
        if not self.isEnabled():
            fill, border, mark = "#E4E7EC", "#D0D5DD", "#98A2B3"
        elif selected:
            fill = "#255C91" if self.isDown() or self.underMouse() else "#2F6FAD"
            border, mark = fill, "#FFFFFF"
        elif self.isDown():
            fill, border, mark = "#EAF1F8", "#8FA6BE", "#FFFFFF"
        elif self.underMouse():
            fill, border, mark = "#F5F8FC", "#8FA6BE", "#FFFFFF"
        else:
            fill, border, mark = "#FFFFFF", "#BCC6D2", "#FFFFFF"

        indicator = QStyle.visualRect(
            self.layoutDirection(),
            self.rect(),
            QRect(
                1,
                (self.height() - self.INDICATOR_SIZE) // 2,
                self.INDICATOR_SIZE,
                self.INDICATOR_SIZE,
            ),
        )
        painter.setPen(QPen(QColor(border), 1.5))
        painter.setBrush(QColor(fill))
        painter.drawRoundedRect(QRectF(indicator).adjusted(0.75, 0.75, -0.75, -0.75), 5, 5)
        if selected:
            path = QPainterPath()
            x, y = indicator.x(), indicator.y()
            if self.checkState() == Qt.CheckState.PartiallyChecked:
                path.moveTo(x + 6, y + 11)
                path.lineTo(x + 16, y + 11)
            else:
                path.moveTo(x + 5.5, y + 11)
                path.lineTo(x + 9.5, y + 15)
                path.lineTo(x + 16.5, y + 7.5)
            painter.setPen(
                QPen(
                    QColor(mark),
                    2.2,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
            )
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

        text_left = self.INDICATOR_SIZE + 2 + self.SPACING
        text_rect = QStyle.visualRect(
            self.layoutDirection(),
            self.rect(),
            QRect(text_left, 0, max(0, self.width() - text_left), self.height()),
        )
        self.style().drawItemText(
            painter,
            text_rect,
            int(
                Qt.AlignmentFlag.AlignLeft
                | Qt.AlignmentFlag.AlignVCenter
                | Qt.TextFlag.TextShowMnemonic
            ),
            self.palette(),
            self.isEnabled(),
            self.text(),
            QPalette.ColorRole.WindowText,
        )


def configure_font(app) -> None:
    translator = QTranslator(app)
    if translator.load(
        "qtbase_zh_CN", QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    ):
        app.installTranslator(translator)
        app._dc_translation = translator
    style_name = app.style().objectName()
    if os.name == "nt":
        available = {name.casefold(): name for name in QStyleFactory.keys()}
        style_name = next(
            (
                available[name]
                for name in ("windows11", "windowsvista", "windows")
                if name in available
            ),
            style_name,
        )
    app.setStyle(style_name)
    # Qt's offscreen platform has no system font database on Windows.
    if not QFontDatabase.families():
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "segmdl2.ttf", "SegoeIcons.ttf"):
            if (fonts / name).is_file():
                QFontDatabase.addApplicationFont(str(fonts / name))
    font = QFont("Microsoft YaHei UI", 10)
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    app.setFont(font)


STYLE = """
QWidget { font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; color: #182230; outline: none; }
QMainWindow { background: transparent; }
QWidget#central { background: transparent; }
QDialog { background: transparent; border: none; }
QFrame#surface { background: #F5F7FA; border: 1px solid #DDE2E8; border-radius: 14px; }
QFrame#windowShadow { background: #F5F7FA; border: none; border-radius: 14px; }
QFrame#dialogBackdrop { background: rgba(24, 34, 48, 41); border: none; border-radius: 14px; }
QWidget#listContent, QScrollArea, QListView { background: transparent; border: none; }
QFrame#card { background: #FFFFFF; border: none; border-radius: 10px; }
QLabel#title { font-size: 20px; font-weight: 600; }
QLabel#name { font-weight: 600; }
QLabel#muted { color: #667085; font-size: 12px; }
QLabel#detailTitle { font-size: 18px; font-weight: 500; }
QLabel#detailPageTitle { font-size: 16px; font-weight: 600; }
QLabel#detailKey { color: #667085; }
QListWidget#detailTabs { background: #FFFFFF; border: none; border-radius: 10px; padding: 8px; }
QListWidget#detailTabs::item { color: #667085; border: none; border-radius: 7px; padding: 0 10px; }
QListWidget#detailTabs::item:hover { background: #F5F7FA; color: #182230; }
QListWidget#detailTabs::item:selected { background: #EAF1F8; color: #2F6FAD; font-weight: 600; }
QListWidget#detailTabs::item:disabled { color: #98A2B3; background: transparent; }
QPlainTextEdit#detailText { background: #FFFFFF; color: #182230; border: none; padding: 0; }
QLabel#error { color: #B42318; }
QLabel#success { color: #287A4B; }
QPushButton { background: #FFFFFF; border: 1px solid #DDE2E8; border-radius: 7px; min-height: 34px; padding: 0 14px; }
QPushButton:hover { color: #2F6FAD; }
QPushButton:focus, QCheckBox:focus { outline: none; }
QPushButton#primary { color: #FFFFFF; background: #2F6FAD; border-color: #2F6FAD; min-height: 40px; padding: 0 22px; }
QPushButton#primary:hover { background: #255C91; }
QPushButton#secondary { min-height: 40px; padding: 0 22px; }
QPushButton:disabled, QPushButton#primary:disabled { background: #E4E7EC; color: #98A2B3; border-color: #E4E7EC; }
QPushButton#textButton, QPushButton#topButton { background: transparent; border: none; color: #667085; padding: 0 8px; }
QPushButton#topButton { font-size: 14px; color: #344054; }
QPushButton#textButton:hover, QPushButton#topButton:hover { color: #2F6FAD; background: #EAF1F8; }
QCheckBox:disabled { color: #98A2B3; }
QProgressBar { border: none; background: #E4E7EC; border-radius: 3px; min-height: 5px; max-height: 5px; }
QProgressBar::chunk { background: #2F6FAD; border-radius: 3px; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: white; border: 1px solid #DDE2E8; border-radius: 6px; min-height: 34px; padding: 0 8px; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: top right; width: 28px; border: none; background: transparent; }
QComboBox::down-arrow { image: url("__CHEVRON_DOWN__"); width: 12px; height: 12px; }
QSpinBox, QDoubleSpinBox { padding-right: 28px; }
QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-origin: padding; subcontrol-position: top right; width: 24px; height: 16px; border: none; background: transparent; }
QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: padding; subcontrol-position: bottom right; width: 24px; height: 16px; border: none; background: transparent; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url("__CHEVRON_UP__"); width: 10px; height: 10px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url("__CHEVRON_DOWN__"); width: 10px; height: 10px; }
QPlainTextEdit, QTextBrowser { background: white; border: 1px solid #DDE2E8; border-radius: 6px; padding: 8px; selection-background-color: #2F6FAD; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: #CCD3DC; min-height: 25px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QGroupBox { border: none; margin-top: 12px; padding-top: 8px; }
""".replace(
    "__CHEVRON_DOWN__", (Path(__file__).parent / "assets/chevron-down.svg").as_posix()
).replace("__CHEVRON_UP__", (Path(__file__).parent / "assets/chevron-up.svg").as_posix())


def button(text: str, callback=None, kind: str = "") -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if callback:
        b.clicked.connect(callback)
    return b


def label(text: str = "", kind: str = "", wrap: bool = False) -> QLabel:
    result = QLabel(text)
    result.setTextFormat(Qt.TextFormat.PlainText)
    result.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextSelectableByMouse
        | Qt.TextInteractionFlag.TextSelectableByKeyboard
    )
    if kind:
        result.setObjectName(kind)
    result.setWordWrap(wrap)
    return result


def window_surface(container: QWidget) -> QFrame:
    layout = QVBoxLayout(container)
    layout.setContentsMargins(10, 10, 10, 14)
    # QStackedLayout can show its pages immediately. Parent them from birth so
    # they can never briefly appear as independent, decorated native windows.
    surface = QFrame(container)
    surface.setObjectName("surface")
    shadow_frame = QFrame(container)
    shadow_frame.setObjectName("windowShadow")
    shadow_frame.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    shadow = QGraphicsDropShadowEffect(shadow_frame)
    shadow.setBlurRadius(16)
    shadow.setOffset(0, 3)
    shadow.setColor(QColor(16, 24, 40, 32))
    shadow_frame.setGraphicsEffect(shadow)
    # Only the background participates in shadow compositing, never text.
    layers = QStackedLayout()
    layout.addLayout(layers)
    layers.setStackingMode(QStackedLayout.StackingMode.StackAll)
    layers.addWidget(shadow_frame)
    layers.addWidget(surface)
    layers.setCurrentWidget(surface)
    return surface


class WindowHeader(QWidget):
    def __init__(self, window: QWidget):
        super().__init__()
        # Resolve the owner with QWidget.window() instead of retaining a parent cycle.
        self._drag_offset = None
        self.setFixedHeight(42)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.title = label(window.windowTitle(), "title")
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        window.windowTitleChanged.connect(self.title.setText)
        self.title.setToolTip(window.windowTitle())
        window.windowTitleChanged.connect(self.title.setToolTip)
        layout.addWidget(self.title, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        self.close_button = button("×", self.close_window, "topButton")
        self.close_button.setFixedWidth(34)
        self.close_button.setAccessibleName("关闭")
        self.close_button.setAutoDefault(False)
        self.actions.addWidget(self.close_button)
        layout.addLayout(self.actions)

    def close_window(self) -> None:
        self.window().close()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            window = self.window()
            handle = window.windowHandle()
            if not handle or not handle.startSystemMove():
                self._drag_offset = event.globalPosition().toPoint() - window.pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class DialogBackdrop(QFrame):
    def __init__(self, surface: QWidget):
        super().__init__(surface)
        self.setObjectName("dialogBackdrop")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.depth = 0
        self.hide()
        surface.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Resize:
            self.setGeometry(watched.rect())
        return super().eventFilter(watched, event)

    def acquire(self) -> None:
        self.depth += 1
        self.setGeometry(self.parentWidget().rect())
        self.raise_()
        self.show()

    def release(self) -> None:
        self.depth -= 1
        if not self.depth:
            self.hide()


class FramelessDialog(QDialog):
    def __init__(self, title: str, parent=None, *, resizable: bool = False):
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle(title)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet(STYLE)
        self.surface = window_surface(self)
        layout = QVBoxLayout(self.surface)
        layout.setContentsMargins(24, 16, 24, 20)
        layout.setSpacing(14)
        self.body = QVBoxLayout()
        self.body.setSpacing(14)
        self.header = WindowHeader(self)
        layout.addWidget(self.header)
        layout.addLayout(self.body, 1)
        self.resize_grip = QSizeGrip(self.surface) if resizable else None
        if self.resize_grip:
            self.resize_grip.setFixedSize(14, 14)
            grip_row = QHBoxLayout()
            grip_row.addStretch()
            grip_row.addWidget(self.resize_grip)
            layout.addLayout(grip_row)

    def exec(self) -> int:
        owner = self.parentWidget()
        while owner is not None and not isinstance(owner, QMainWindow):
            owner = owner.parentWidget()
        backdrop = None
        if owner is not None:
            backdrop = getattr(owner, "_dialog_backdrop", None)
            if backdrop is None:
                backdrop = DialogBackdrop(getattr(owner, "surface", owner))
                owner._dialog_backdrop = backdrop
            backdrop.acquire()
        try:
            return super().exec()
        finally:
            if backdrop is not None:
                backdrop.release()


class MessageDialog(FramelessDialog):
    def __init__(
        self,
        title: str,
        text: str,
        parent=None,
        *,
        accept_text: str = "确定",
        reject_text: str = "",
    ):
        super().__init__(title, parent)
        self.setMinimumWidth(420)
        self.message = label(text, wrap=True)
        self.message.setMaximumWidth(560)
        self.body.addWidget(self.message)
        row = QHBoxLayout()
        row.addStretch()
        if reject_text:
            self.reject_button = button(reject_text, self.reject)
            self.reject_button.setDefault(True)
            row.addWidget(self.reject_button)
        self.accept_button = button(accept_text, self.accept, "primary")
        self.accept_button.setAutoDefault(not reject_text)
        self.accept_button.setDefault(not reject_text)
        row.addWidget(self.accept_button)
        self.body.addLayout(row)


class DirectoryDialog(FramelessDialog):
    def __init__(self, directory: Path, parent=None):
        super().__init__("选择目录", parent, resizable=True)
        self.resize(760, 540)
        self.setMinimumSize(640, 460)
        self.picker = QFileDialog(self, directory=str(directory))
        self.picker.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        self.picker.setFileMode(QFileDialog.FileMode.Directory)
        self.picker.setOption(QFileDialog.Option.ShowDirsOnly, True)
        for key, text in (
            (QFileDialog.DialogLabel.LookIn, "位置"),
            (QFileDialog.DialogLabel.FileName, "目录"),
            (QFileDialog.DialogLabel.FileType, "类型"),
            (QFileDialog.DialogLabel.Accept, "选择文件夹"),
            (QFileDialog.DialogLabel.Reject, "取消"),
        ):
            self.picker.setLabelText(key, text)
        self.picker.setWindowFlags(Qt.WindowType.Widget)
        self.picker.setSizeGripEnabled(False)
        self.picker.accepted.connect(self.accept)
        self.picker.rejected.connect(self.reject)
        self.body.addWidget(self.picker, 1)


def show_message(parent: QWidget | None, title: str, text: str) -> None:
    MessageDialog(title, text, parent).exec()


def open_path(parent: QWidget, path: Path) -> None:
    if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
        show_message(parent, "无法打开", str(path))


def open_text_file(parent: QWidget, path: Path) -> None:
    if os.name != "nt":
        open_path(parent, path)
        return
    editor = Path(os.environ.get("WINDIR", "C:/Windows")) / "System32" / "notepad.exe"
    started, _ = QProcess.startDetached(str(editor), [str(path.resolve())])
    if not started:
        show_message(parent, "无法打开文本编辑器", f"请手动编辑配置：\n{path}")


def size_text(size: int) -> str:
    for factor, unit in ((1_073_741_824, "GB"), (1_048_576, "MB"), (1_024, "KB")):
        if size >= factor:
            return f"{size / factor:.2f} {unit}"
    return f"{size} B"


def elapsed_text(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60:02d}:{s % 60:02d}"
