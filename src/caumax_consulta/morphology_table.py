"""Compact, bounded interpretation text while retaining the full model value."""
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QFontMetrics, QTextLayout, QTextOption
from qgis.PyQt.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem


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
