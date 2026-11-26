"""Shared reviewed disk cleanup for the GEA row and D8 window."""
from pathlib import Path

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QMessageBox, QPushButton


def clean_button(parent):
    button = QPushButton('Limpiar capas', parent)
    button.setIcon(QIcon(str(Path(__file__).parent/'assets'/'clear_generated_layers.svg')))
    button.setStyleSheet('QPushButton { background-color: #FADBD8; border: 1px solid #CF8D89; '
        'border-radius: 3px; padding: 2px 4px; } QPushButton:hover { background-color: #F5B7B1; } '
        'QPushButton:disabled { background-color: #F3E8E7; color: #888888; }')
    button.setToolTip('Retirar resultados y referencias de HidroCAUMAX; revisar el borrado de archivos generados. '
                      'Los archivos de referencia se conservan.')
    return button


def confirm_cleanup(parent):
    from .generated_cleanup import deletion_plan, delete_generated
    plan = deletion_plan()
    if not (plan['layers'] or plan['files'] or plan.get('reference_groups') or plan.get('references_loading')):
        return 'No hay resultados ni referencias de HidroCAUMAX para limpiar.'
    prompt = QMessageBox(parent)
    prompt.setWindowTitle('Limpiar capas de HidroCAUMAX')
    prompt.setIcon(QMessageBox.Warning if plan['files'] else QMessageBox.Question)
    prompt.setText(f"Se retirarán {len(plan['layers'])} capas de QGIS y se eliminarán "
                   f"{len(plan['files'])} archivos del disco duro.")
    text = ('Se retirarán también las referencias de HidroCAUMAX y sus grupos vacíos; '
        'se cancelará su carga si está pendiente. Los archivos de referencia, incluido P0, se conservan.')
    if plan['files']:
        text += (' Se incluyen archivos generados registrados y temporales de sesiones anteriores. '
                 'El borrado de esos archivos no se puede deshacer.')
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
    result = delete_generated(plan)
    from .improved_storage import preserve_exports
    try:
        preserve_exports()
    except OSError as error:
        QMessageBox.warning(parent, 'Conservación de exportaciones', str(error))
    message = f"Se han retirado {result['layers']} capas y eliminado {len(result['removed'])} archivos."
    if result['failed']:
        message += f" No se han podido eliminar {len(result['failed'])} archivos."
        QMessageBox.warning(parent, 'Archivos conservados', '\n'.join(result['failed']))
    return message
