"""Compact interpretation cells and an in-column action without text overlap."""
from pathlib import Path

from qgis.PyQt.QtCore import QEvent, QRect, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import QFont, QFontMetrics, QIcon, QPalette, QTextLayout, QTextOption
from qgis.PyQt.QtWidgets import (
    QApplication, QGroupBox, QHeaderView, QPushButton, QStyle, QStyledItemDelegate,
    QStyleOptionGroupBox, QStyleOptionHeader, QStyleOptionViewItem, QToolTip,
)


class ActionGroupBox(QGroupBox):
    """Keep a compact action level with the native group title."""
    def set_header_action(self, button):
        self.header_action = button
        button.setParent(self)
        button.setStyleSheet(button.styleSheet() + '\nQPushButton { padding: 0px 8px; margin: 0px; }')
        self._layout_header_action()

    def title_rect(self):
        option = QStyleOptionGroupBox()
        self.initStyleOption(option)
        return self.style().subControlRect(QStyle.CC_GroupBox, option, QStyle.SC_GroupBoxLabel, self)

    def _layout_header_action(self):
        button = getattr(self, 'header_action', None)
        if button is None:
            return
        title = self.title_rect()
        height = max(title.height(), QFontMetrics(button.font()).height() + 2, 20)
        button.setIconSize(QSize(min(18, height - 2), min(18, height - 2)))
        button.setFixedHeight(height)
        width = button.sizeHint().width()
        right_margin = self.layout().contentsMargins().right() if self.layout() is not None else 11
        button.setGeometry(max(title.right() + 8, self.width() - right_margin - width),
                           max(0, title.center().y() - height // 2), width, height)
        button.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._layout_header_action()

    def event(self, event):
        result = super().event(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange, QEvent.LayoutDirectionChange):
            self._layout_header_action()
        return result


class InterpretationHeader(QHeaderView):
    """Keep the interpretation label and AI action inside logical column 3.

    Only this section's painted label and child button may use a smaller local
    font. The table and the other headers retain their application fonts.
    ``update_layout`` also supports callers that resize sections with signals
    blocked; paint/resize/scroll events provide the normal automatic updates.
    """
    analysis_requested = pyqtSignal()
    COLUMN = 3
    TITLE = 'Interpretación'
    _LEFT_PADDING = 5
    _GAP = 6
    _BUTTON_INSET = 10  # Two 4px paddings and two 1px borders.
    _ICON_GAP = 4

    def __init__(self, table):
        super().__init__(Qt.Horizontal, table)
        self._title_font = QFont(self.font())
        self._title_rect = QRect()
        self._title_text = self.TITLE
        self.analysis_button = QPushButton('Análisis IA', self.viewport())
        self.analysis_button.setObjectName('HidroCAUMAXInterpretationAI')
        self.analysis_button.setAutoDefault(False)
        self.analysis_button.setIcon(QIcon(str(Path(__file__).parent / 'assets' / 'analysis_ai.svg')))
        self.analysis_button.setLayoutDirection(Qt.LeftToRight)
        self.analysis_button.setToolTip('Generar las 19 interpretaciones de la cuenca con IA.')
        self.analysis_button.setStyleSheet('''
            QPushButton { background-color: #174D78; color: white;
                border: 1px solid #103B5C; border-radius: 3px;
                padding: 0px 4px; margin: 0px; min-height: 0px; min-width: 0px; }
            QPushButton:hover { background-color: #216493; }
            QPushButton:pressed { background-color: #103B5C; }
            QPushButton:disabled { background-color: #91A5B5; color: #F4F7FA;
                border-color: #8298A9; }
        ''')
        self.analysis_button.clicked.connect(lambda _=False: self.analysis_requested.emit())
        self.sectionResized.connect(self.update_layout)
        self.sectionMoved.connect(self.update_layout)
        self.geometriesChanged.connect(self.update_layout)
        table.horizontalScrollBar().valueChanged.connect(self.update_layout)
        self.update_layout()

    def _fonts(self):
        """Local sizes from the real header font down to a readable fallback."""
        base = QFont(self.font())
        if base.pixelSize() > 0:
            for size in range(base.pixelSize(), min(base.pixelSize(), 9) - 1, -1):
                font = QFont(base)
                font.setPixelSize(size)
                yield font
        else:
            size = base.pointSizeF()
            minimum = min(size, 7.)
            while size >= minimum:
                font = QFont(base)
                font.setPointSizeF(size)
                yield font
                if size == minimum:
                    break
                size = max(minimum, size - .25)

    def update_layout(self, *args):
        if not hasattr(self, 'analysis_button'):
            return
        if self.count() <= self.COLUMN or self.isSectionHidden(self.COLUMN):
            self.analysis_button.hide()
            self._title_rect = QRect()
            return
        width, height = self.sectionSize(self.COLUMN), self.viewport().height()
        left = self.sectionViewportPosition(self.COLUMN)
        # Match the toolbar's white neural icon, retaining a little vertical
        # breathing room when the application uses a compact header height.
        icon_extent = max(1, min(18, height - 4))
        self.analysis_button.setIconSize(QSize(icon_extent, icon_extent))
        icon_space = icon_extent + self._ICON_GAP
        title_font = QFont(self.font())
        for font in self._fonts():
            title_font = font
            metrics = QFontMetrics(font)
            button_width = metrics.horizontalAdvance(self.analysis_button.text()) + self._BUTTON_INSET + icon_space
            if metrics.horizontalAdvance(self.TITLE) + button_width + self._LEFT_PADDING + self._GAP <= width:
                break
        metrics = QFontMetrics(title_font)
        button_width = metrics.horizontalAdvance(self.analysis_button.text()) + self._BUTTON_INSET + icon_space
        self._title_font = title_font
        self.analysis_button.setFont(title_font)
        # An exceptionally narrow/hidden section must not put its action over
        # the neighbouring headers. The upper toolbar action remains available.
        show_button = width >= button_width + self._LEFT_PADDING
        self.analysis_button.setVisible(show_button)
        if show_button:
            self.analysis_button.setGeometry(left + width - button_width, 0, button_width, height)
        text_width = max(0, width - self._LEFT_PADDING - (button_width + self._GAP if show_button else 0))
        self._title_rect = QRect(left + self._LEFT_PADDING, 0, text_width, height)
        self._title_text = metrics.elidedText(self.TITLE, Qt.ElideRight, text_width)
        self.analysis_button.raise_()

    def paintEvent(self, event):
        self.update_layout()
        super().paintEvent(event)

    def paintSection(self, painter, rect, logical_index):
        if logical_index != self.COLUMN:
            super().paintSection(painter, rect, logical_index)
            return
        option = QStyleOptionHeader()
        self.initStyleOption(option)
        option.rect = rect
        option.section = logical_index
        option.text = ''
        self.style().drawControl(QStyle.CE_Header, option, painter, self)
        painter.save()
        painter.setClipRect(self._title_rect.intersected(rect))
        painter.setFont(self._title_font)
        painter.setPen(self.palette().color(QPalette.ButtonText))
        painter.drawText(self._title_rect, Qt.AlignLeft | Qt.AlignVCenter, self._title_text)
        painter.restore()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_layout()

    def event(self, event):
        result = super().event(event)
        if event.type() in (QEvent.FontChange, QEvent.StyleChange, QEvent.LayoutDirectionChange):
            self.update_layout()
        return result

    def viewportEvent(self, event):
        if event.type() == QEvent.ToolTip and self.logicalIndexAt(event.pos()) == self.COLUMN:
            detail = 'Interpretación · Análisis IA: genera las 19 interpretaciones de la cuenca.'
            if self._title_text != self.TITLE or self.analysis_button.isHidden():
                detail += ' Amplía esta columna o usa el botón superior «Análisis IA».'
            QToolTip.showText(event.globalPos(), detail, self)
            return True
        return super().viewportEvent(event)


def three_line_text(text, font, width):
    text = ' '.join(str(text).split())
    width = max(1, width)
    layout = QTextLayout(text, font)
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(option)
    layout.beginLayout()
    parts = []
    for index in range(3):
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(width)
        start, end = line.textStart(), line.textStart() + line.textLength()
        if index == 2 and end < len(text):
            parts.append(QFontMetrics(font).elidedText(text[start:], Qt.ElideRight, width))
        else:
            parts.append(text[start:end].strip())
    layout.endLayout()
    return '\n'.join(parts)


class InterpretationDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        style = styled.widget.style() if styled.widget is not None else QApplication.style()
        text_rect = style.subElementRect(QStyle.SE_ItemViewItemText, styled, styled.widget)
        styled.text = three_line_text(styled.text, styled.font, text_rect.width() - 6)
        style.drawControl(QStyle.CE_ItemViewItem, styled, painter, styled.widget)

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        metrics=QFontMetrics(option.font)
        size.setHeight(min(size.height(), max(metrics.height(),metrics.lineSpacing()) * 3 + 6))
        return size


def fit_morphology_rows(table):
    """Fit body rows without the hidden vertical header's padded section size.

    Keep six pixels of breathing room for a single line. Wrapped parameter
    names and interpretations retain the height required by their delegates;
    the interpretation delegate still limits the visible text to three lines.
    This does not change the horizontal header or any font.
    """
    metrics = table.fontMetrics()
    minimum = max(metrics.height(), metrics.lineSpacing()) + 6
    table.verticalHeader().setMinimumSectionSize(minimum)
    for row in range(table.rowCount()):
        table.setRowHeight(row, max(minimum, table.sizeHintForRow(row)))
