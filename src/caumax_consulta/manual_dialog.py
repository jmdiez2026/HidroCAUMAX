"""Offline manual dialog; page previews are rendered from the bundled PDF."""
import hashlib
import json
from pathlib import Path

from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtGui import QDesktopServices, QPixmap
from qgis.PyQt.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QScrollArea, QVBoxLayout)

MANUAL_DIR = Path(__file__).resolve().parent/'docs'/'manual'
PDF_NAME = 'Manual_HidroCAUMAX_v1.0.pdf'


class ManualDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('HidroCAUMAX v.1.0 · Ayuda y manual de usuario')
        self.setObjectName('HidroCAUMAXManual')
        self.resize(820, 780)
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        controls.addWidget(QLabel('Página:'))
        self.pages = QComboBox()
        controls.addWidget(self.pages, 1)
        self.open_pdf = QPushButton('Abrir PDF')
        self.open_pdf.setToolTip('Abrir el manual completo con el lector PDF del equipo.')
        self.open_pdf.clicked.connect(self._open_pdf)
        controls.addWidget(self.open_pdf)
        layout.addLayout(controls)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.page = QLabel()
        self.page.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.scroll.setWidget(self.page)
        layout.addWidget(self.scroll, 1)
        close = QPushButton('Cerrar')
        close.clicked.connect(self.close)
        bottom = QHBoxLayout(); bottom.addStretch(); bottom.addWidget(close)
        layout.addLayout(bottom)
        self._pixmap = QPixmap()
        try:
            self._document = json.loads((MANUAL_DIR/'pages.json').read_text(encoding='utf-8'))
            if hashlib.sha256((MANUAL_DIR/PDF_NAME).read_bytes()).hexdigest() != self._document['pdf_sha256']:
                raise ValueError('El PDF no coincide con las páginas del manual.')
            for number, title in enumerate(self._document['titles'], 1):
                self.pages.addItem(f'{number} · {title}')
            self.pages.currentIndexChanged.connect(self._show_page)
            self._show_page(0)
        except (OSError, ValueError, KeyError) as error:
            self.page.setText('No se ha podido abrir el manual incluido. ' + str(error))
            self.pages.setEnabled(False)
            self.open_pdf.setEnabled(False)

    def _show_page(self, index):
        self._pixmap = QPixmap(str(MANUAL_DIR/f'page-{index+1:02d}.png'))
        if self._pixmap.isNull():
            self.page.setText('No se ha podido mostrar esta página. Utiliza Abrir PDF.')
            return
        self._fit_page()
        self.scroll.verticalScrollBar().setValue(0)

    def _fit_page(self):
        if not self._pixmap.isNull():
            width = max(200, self.scroll.viewport().width()-20)
            scaled = self._pixmap.scaledToWidth(width, Qt.SmoothTransformation)
            self.page.setPixmap(scaled)
            self.page.setMinimumHeight(scaled.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_pixmap'):
            self._fit_page()

    def _open_pdf(self):
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(MANUAL_DIR/PDF_NAME))):
            QMessageBox.warning(self, 'Manual de usuario',
                'El equipo no ha podido abrir el lector PDF. Puedes leer el manual en este diálogo.')
