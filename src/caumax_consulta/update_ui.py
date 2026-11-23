"""Nonmodal update notices; installation remains in QGIS's plugin manager."""
from qgis.PyQt.QtCore import QObject, QTimer
from qgis.PyQt.QtWidgets import QApplication, QDialog, QLineEdit, QPushButton
from qgis.core import Qgis, QgsSettings

from .update_checker import REPOSITORY_URL, UpdateChecker


def ensure_repository(settings=None):
    """Called only on the user's Update click; preserve existing repositories."""
    settings = QgsSettings() if settings is None else settings
    settings.beginGroup('app/plugin_repositories')
    try:
        names = settings.childGroups()
        for name in names:
            if settings.value(name + '/url', '', type=str).strip() == REPOSITORY_URL:
                return name, settings.value(name + '/enabled', True, type=bool), False
        name = 'HidroCAUMAX'
        number = 2
        while name in names:
            name = f'HidroCAUMAX ({number})'
            number += 1
        settings.setValue(name + '/url', REPOSITORY_URL)
        settings.setValue(name + '/enabled', True)
        return name, True, True
    finally:
        settings.endGroup()


class UpdateNotifier(QObject):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.checker = UpdateChecker(self)
        self.checker.available.connect(self._available)
        self.checker.completed.connect(self._completed)
        self._seen = set()
        self._manual = False
        self._disposed = False
        self._notices = []
        self._update_notice = None
        self._filter_timer = QTimer(self)
        self._filter_timer.setInterval(100)
        self._filter_timer.timeout.connect(self._filter_manager)

    def check(self, manual=False):
        if self._disposed:
            return False
        # A manual request made while an automatic GET is pending shares it,
        # but receives an explicit result once that same request finishes.
        self._manual = self._manual or bool(manual)
        return self.checker.check()

    def _message(self, text, level=Qgis.Info, duration=8, update=False):
        if self._disposed:
            return
        try:
            bar = self.iface.messageBar()
            widget = bar.createMessage('HidroCAUMAX', text)
            if update:
                if self._update_notice is not None:
                    try:
                        bar.popWidget(self._update_notice)
                    except RuntimeError:
                        pass
                button = QPushButton('Actualizar', widget)
                button.setToolTip('Abrir el gestor de complementos de QGIS. La instalación requiere tu acción.')
                button.clicked.connect(self.open_manager)
                widget.layout().addWidget(button)
                self._update_notice = widget
            self._notices.append(widget)
            bar.pushWidget(widget, level, duration)
        except (AttributeError, RuntimeError):
            # QGIS may already be closing; an update notice must not interrupt it.
            return

    def _available(self, info):
        if self._disposed:
            return
        version = info['version']
        if self._manual or version not in self._seen:
            self._seen.add(version)
            self._message(
                f"Versión {version} disponible; instalada: {info['installed_version']}. "
                'Puedes revisarla e instalarla en el gestor de complementos.',
                duration=0, update=True)

    def _completed(self, status, detail):
        manual, self._manual = self._manual, False
        if self._disposed or not manual or status in ('available', 'cancelled'):
            return
        level = Qgis.Info if status == 'current' else Qgis.Warning
        self._message(detail, level=level)

    def open_manager(self, *args):
        if self._disposed:
            return
        try:
            _, enabled, _ = ensure_repository()
            if not enabled:
                self._message(
                    'El repositorio de HidroCAUMAX está desactivado. Puedes activarlo en '
                    'la configuración del gestor; se ha conservado tu ajuste.', level=Qgis.Warning)
            import pyplugin_installer
            installer = pyplugin_installer.instance()
            # These APIs may show QGIS's own progress/manager dialogs. They are
            # deliberately called only in response to the Update button.
            installer.reloadAndExportData()
            self._filter_manager()
            self._filter_timer.start()
            installer.showPluginManagerWhenReady(0)
        except Exception:
            self._message(
                'No se ha podido abrir el gestor. Puedes acceder desde '
                'Complementos > Administrar e instalar complementos…', level=Qgis.Warning)
        finally:
            self._filter_timer.stop()

    def _filter_manager(self):
        if self._disposed:
            self._filter_timer.stop()
            return
        app = QApplication.instance()
        if app is None:
            return
        for dialog in app.topLevelWidgets():
            if isinstance(dialog, QDialog) and dialog.objectName() == 'QgsPluginManagerBase':
                field = dialog.findChild(QLineEdit, 'leFilter')
                if field is not None:
                    field.setText('HidroCAUMAX')
                    self._filter_timer.stop()
                    return

    def _clear_notices(self):
        notices, self._notices = self._notices, []
        self._update_notice = None
        for widget in notices:
            try:
                self.iface.messageBar().popWidget(widget)
            except (AttributeError, RuntimeError):
                pass

    def cancel(self):
        self._manual = False
        self.checker.cancel()
        self._clear_notices()

    def dispose(self):
        if self._disposed:
            return
        self._disposed = True
        self._manual = False
        self._filter_timer.stop()
        self.checker.available.disconnect(self._available)
        self.checker.completed.disconnect(self._completed)
        self.checker.dispose()
        self._clear_notices()
