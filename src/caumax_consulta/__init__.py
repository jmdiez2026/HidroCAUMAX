"""Consulta auxiliar de celdas CAUMAX para QGIS 3."""


def classFactory(iface):
    from .native_plugin import NativeCaumaxPlugin

    return NativeCaumaxPlugin(iface)
