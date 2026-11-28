"""Compact automatic interpretation cells with complete accessible tooltips."""
from qgis.PyQt.QtCore import QEvent, Qt
from qgis.PyQt.QtGui import QFontMetrics, QTextLayout, QTextOption
from qgis.PyQt.QtWidgets import (
    QApplication, QHeaderView, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem, QToolTip,
)


class InterpretationHeader(QHeaderView):
    """A normal header: interpretation is computed without an action button."""
    COLUMN = 2

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



def wrapped_line_count(text, font, width):
    """Count complete wrapped lines, without eliding a parameter label."""
    layout = QTextLayout(str(text), font)
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(option)
    layout.beginLayout()
    count = 0
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(max(1, int(width)))
        count += 1
    layout.endLayout()
    return max(1, count)


def compact_parameter_width(table, available_width, *, max_lines=3):
    """Narrowest QGIS parameter column fitting complete labels in 3 lines.

    The interpretation has priority over empty space. On smaller viewports
    labels may use additional lines; row heights expand so none are cut.
    """
    metrics = table.fontMetrics()
    labels = [table.item(row, 0).text() for row in range(table.rowCount())
              if table.item(row, 0) is not None]
    left = max(135, metrics.horizontalAdvance('Parámetro') + 16)
    right = max(left, *(metrics.horizontalAdvance(text) + 18 for text in labels))
    while left < right:
        middle = (left + right) // 2
        if all(wrapped_line_count(text, table.font(), middle - 12) <= max_lines
               for text in labels):
            right = middle
        else:
            left = middle + 1
    # Guard at least a third of the actual viewport for Interpretation;
    # value column is sized separately in the caller.
    return min(left, max(135, int(available_width * .40)))


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
        name = table.item(row, 0)
        name_lines = wrapped_line_count(name.text(), table.font(),
                                        table.columnWidth(0) - 12) if name else 1
        label_height = name_lines * max(metrics.height(), metrics.lineSpacing()) + 8
        table.setRowHeight(row, max(minimum, label_height,
                                    table.sizeHintForRow(row)))
