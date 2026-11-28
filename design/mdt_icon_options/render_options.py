"""Render the three pending SVG choices, without applying one to the plugin."""
from pathlib import Path
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtCore import QRectF, Qt
from PyQt5.QtGui import QColor, QFont, QImage, QPainter, QPen, QIcon
from PyQt5.QtSvg import QSvgRenderer
from PyQt5.QtWidgets import QApplication, QPushButton

ROOT = Path(__file__).resolve().parent
app = QApplication.instance() or QApplication([])
app.setFont(QFont("DejaVu Sans", 10))
canvas = QImage(1320, 630, QImage.Format_ARGB32)
canvas.fill(QColor("#F5F8FC"))
p = QPainter(canvas)
p.setRenderHint(QPainter.Antialiasing)
p.setRenderHint(QPainter.TextAntialiasing)


def label(rect, text, size, color="#173D52", weight=QFont.Normal, alignment=Qt.AlignLeft | Qt.AlignVCenter):
    p.setPen(QColor(color))
    p.setFont(QFont("DejaVu Sans", size, weight))
    p.drawText(QRectF(*rect), alignment, text)


label((45, 28, 1230, 32), "HIDROCAUMAX · MÉTODO RACIONAL", 12, "#638293", QFont.DemiBold)
label((45, 74, 1230, 52), "Mejorar resolución MDT", 28, "#173D52", QFont.DemiBold)
label((45, 132, 1230, 30), "Tres opciones de icono · Elige 1, 2 o 3", 14, "#638293")

titles = ["Malla más fina", "Relieve con lupa", "Modelo del terreno"]
descriptions = ["De celdas grandes a pequeñas", "Detalle del relieve", "MDT y mejora de resolución"]
style = """QPushButton { background-color: #D9F0FF; color: #173D52;
border: 1px solid #7EB4D0; border-radius: 3px; padding: 5px 9px; }"""
for index in range(3):
    x = 45 + index * 420
    p.setBrush(QColor("white"))
    p.setPen(QPen(QColor("#DCE6EF"), 1.1))
    p.drawRoundedRect(QRectF(x, 195, 390, 375), 16, 16)
    p.setBrush(QColor("#E9F4FB"))
    p.setPen(Qt.NoPen)
    p.drawEllipse(QRectF(x + 22, 217, 36, 36))
    label((x + 22, 216, 36, 36), str(index + 1), 14, "#176D9D", QFont.DemiBold, Qt.AlignCenter)
    svg = ROOT / f"mejorar_resolucion_mdt_modelo{index + 1}.svg"
    renderer = QSvgRenderer(str(svg))
    assert renderer.isValid(), svg
    renderer.render(p, QRectF(x + 139, 263, 112, 112))
    label((x + 20, 388, 350, 32), titles[index], 18, "#173D52", QFont.DemiBold, Qt.AlignCenter)
    label((x + 15, 427, 360, 25), descriptions[index], 11, "#638293", alignment=Qt.AlignCenter)
    button = QPushButton("Mejorar resolución MDT")
    button.setIcon(QIcon(str(svg)))
    from PyQt5.QtCore import QSize
    button.setIconSize(QSize(22, 22))
    button.setStyleSheet(style)
    button.setLayoutDirection(Qt.LeftToRight)
    button.resize(button.sizeHint())
    button.ensurePolished()
    button.grab().toImage().save(str(ROOT / f"button_modelo{index + 1}.png"))
    button_image = button.grab().toImage()
    p.drawImage(int(x + (390 - button_image.width()) / 2), 493, button_image)

label((45, 586, 1230, 24), "Iconos vectoriales en azul · Vista ampliada y botón con icono de 22 px", 11, "#638293")
p.end()
path = ROOT / "HidroCAUMAX_opciones_icono_MDT.png"
assert canvas.save(str(path))
print(path)
