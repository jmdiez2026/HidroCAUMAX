"""Default P0 presentation: RdYlGn, linear, 16 quantiles, integer labels."""
from qgis.PyQt.QtGui import QColor
from qgis.core import (QgsGradientColorRamp, QgsGradientStop, QgsColorRampShader,
                       QgsRasterShader, QgsSingleBandPseudoColorRenderer)


def apply_po_style(layer):
    ramp = QgsGradientColorRamp(QColor('#d7191c'), QColor('#1a9641'), False,
        [QgsGradientStop(0.25, QColor('#fdae61')), QgsGradientStop(0.5, QColor('#ffffbf')),
         QgsGradientStop(0.75, QColor('#a6d96a'))])
    colors = QgsColorRampShader(0, 152, ramp, QgsColorRampShader.Interpolated, QgsColorRampShader.Quantile)
    colors.setLabelPrecision(0)
    colors.classifyColorRamp(16, 1, layer.extent(), layer.dataProvider())
    items = colors.colorRampItemList()
    for item in items:
        item.label = f'{item.value:.0f}'
    colors.setColorRampItemList(items)
    colors.setClip(False)
    shader = QgsRasterShader(0, 152)
    shader.setRasterShaderFunction(colors)
    renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader)
    renderer.setClassificationMin(0)
    renderer.setClassificationMax(152)
    layer.setRenderer(renderer)
    layer.setCustomProperty('HidroCAUMAX/po_style', 'RdYlGn-quantile16-v1')
    layer.triggerRepaint()
