"""Content-sized independent window for the rational D8 tools."""
from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtWidgets import QApplication, QDialog


class RationalLayersDialog(QDialog):
    def fit_content(self, panel, scroll):
        self._panel, self._scroll = panel, scroll
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.timeout.connect(self._fit_content)
        if hasattr(panel, 'content_changed'):
            panel.content_changed.connect(self._schedule_fit)
        self._fit_timer.start(0)

    def _schedule_fit(self):
        self._fit_timer.start(0)

    def _fit_content(self):
        panel, scroll = self._panel, self._scroll
        available = (self.screen() or QApplication.primaryScreen()).availableGeometry()
        margins = self.layout().contentsMargins()
        # Natural control width, capped by the available monitor. Longer
        # explanations wrap; a scroll area remains for smaller displays.
        row = getattr(panel, '_drainage_controls_layout', None)
        controls = getattr(panel, '_drainage_controls', ())
        row_width = sum(widget.sizeHint().width() for widget in controls)
        row_width += row.spacing() * (len(controls) - 1) if row else 0
        width = min(max(760, row_width + 64), max(300, available.width() - 40))
        inner = width - margins.left() - margins.right()
        if hasattr(panel, 'fit_to_width'):
            panel.fit_to_width(inner)
        body = panel.layout().heightForWidth(inner)
        # QGroupBox's title/frame margins are outside the layout's body hint.
        # Include them so progress/cancellation updates do not add a needless
        # vertical scrollbar on a monitor with enough room.
        frame_margins = panel.contentsMargins()
        body = max(body, panel.minimumHeight()) + frame_margins.top() + frame_margins.bottom()
        footer = self.layout().itemAt(self.layout().count() - 1).sizeHint().height()
        height = max(1, body) + margins.top() + margins.bottom() + footer + self.layout().spacing() + 12
        self.resize(width, min(height, max(240, available.height() - 60)))
        # Content can grow after an error/progress update near a screen edge.
        frame = self.frameGeometry()
        self.move(max(available.left(), min(self.x(), available.right() - frame.width() + 1)),
                  max(available.top(), min(self.y(), available.bottom() - frame.height() + 1)))
