"""Compact automatic interpretation cells with complete accessible tooltips."""
from qgis.PyQt.QtCore import QEvent, Qt
from qgis.PyQt.QtGui import QFontMetrics, QTextLayout, QTextOption
from qgis.PyQt.QtWidgets import (
    QApplication, QHeaderView, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem, QToolTip,
)


class InterpretationHeader(QHeaderView):
    """A normal header: interpretation is computed without an action button."""
    COLUMN = 3

    def __init__(self, table):
        super().__init__(Qt.Horizontal, table)

    def viewportEvent(self, event):
        if event.type() == QEvent.ToolTip and self.logicalIndexAt(event.pos()) == self.COLUMN:
            QToolTip.showText(event.globalPos(),
                'Interpretación automática · criterios del tema Cuenca Hidrográfica. '
                'Sitúa el puntero sobre una celda para leer el comentario completo.', self)
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
