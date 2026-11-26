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
    button.setToolTip('Retirar las capas generadas y eliminar sus archivos, previa revisión de la lista.')
    return button


def confirm_cleanup(parent):
    from .generated_cleanup import deletion_plan, delete_generated
    plan = deletion_plan()
    if not plan['layers'] and not plan['files']:
        return 'No hay capas ni archivos generados por HidroCAUMAX para eliminar.'
    prompt = QMessageBox(parent)
    prompt.setWindowTitle('Limpiar capas de HidroCAUMAX')
    prompt.setIcon(QMessageBox.Warning)
    prompt.setText(f"Se retirarán {len(plan['layers'])} capas de QGIS y se eliminarán "
                   f"{len(plan['files'])} archivos del disco duro.")
    prompt.setInformativeText('Se incluyen las exportaciones registradas en este proyecto y las capas '
        'temporales de sesiones anteriores. Los datos originales se conservan. Esta acción no se puede deshacer.')
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
