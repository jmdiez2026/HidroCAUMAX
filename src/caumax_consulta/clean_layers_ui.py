"""Shared reviewed disk cleanup for the GEA row and D8 window."""
from pathlib import Path

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QMessageBox, QPushButton
from qgis.core import Qgis, QgsMessageLog


def clean_button(parent):
    button = QPushButton('Limpiar capas', parent)
    button.setIcon(QIcon(str(Path(__file__).parent/'assets'/'clear_generated_layers.svg')))
    button.setStyleSheet('QPushButton { background-color: #FADBD8; border: 1px solid #CF8D89; '
        'border-radius: 3px; padding: 2px 4px; } QPushButton:hover { background-color: #F5B7B1; } '
        'QPushButton:disabled { background-color: #F3E8E7; color: #888888; }')
    button.setToolTip('Eliminar capas y resultados HidroCAUMAX de QGIS, disco, copias de seguridad y cachés '
                      'temporales. Conserva los archivos oficiales de referencia y el P0 incluido.')
    return button


def confirm_cleanup(parent, iface=None):
    from .generated_cleanup import deletion_plan, delete_generated
    plan = deletion_plan()
    if not (plan['layers'] or plan['files'] or plan.get('reference_groups') or plan.get('references_loading')):
        return 'No hay resultados ni referencias de HidroCAUMAX para limpiar.'
    prompt = QMessageBox(parent)
    prompt.setWindowTitle('Limpiar capas de HidroCAUMAX')
    prompt.setIcon(QMessageBox.Warning if plan['files'] else QMessageBox.Question)
    prompt.setText(f"Se retirarán {len(plan['layers'])} capas de QGIS y se eliminarán "
                   f"{len(plan['files'])} archivos del disco duro.")
    text = ('Se eliminarán los resultados de HidroCAUMAX de QGIS, las exportaciones '
        'generadas, las copias conservadas y las cachés temporales. También se cancelarán '
        'las tareas activas. Solo se conservarán las fuentes originales de CAUMAX, '
        'los archivos oficiales de referencia y el P0 incluido.')
    if plan['files']:
        text += (' Se incluyen archivos generados de sesiones anteriores y sus auxiliares. '
                 'El borrado no se puede deshacer. Si Windows bloquea algún archivo, '
                 'se mostrará una incidencia específica.')
    prompt.setInformativeText(text)
    prompt.setDetailedText('Capas:\n' + '\n'.join(plan['layers'].values()) +
        '\n\nArchivos:\n' + '\n'.join(sorted(plan['files'])) +
        '\n\nOrígenes conservados:\n' + '\n'.join(plan['protected']))
    remove = prompt.addButton('Limpiar capas', QMessageBox.DestructiveRole)
    cancel = prompt.addButton('Cancelar', QMessageBox.RejectRole)
    prompt.setDefaultButton(cancel)
    prompt.exec()
    if prompt.clickedButton() is not remove:
        return None
    canvas = None
    if iface is not None:
        getter = getattr(iface, 'mapCanvas', None)
        if callable(getter):
            canvas = getter()
    result = delete_generated(plan, canvas=canvas)
    message = (f"Se han retirado {result['layers']} capas y eliminado "
               f"{len(result['removed'])} archivos generados, incluidos los temporales y respaldos.")
    if result['failed']:
        message += f" No se han podido eliminar {len(result['failed'])} archivos."
        QgsMessageLog.logMessage('No se pudieron eliminar estos archivos:\n'
                                 + '\n'.join(result['failed']),
                                 'HidroCAUMAX', Qgis.Warning)
    return message
