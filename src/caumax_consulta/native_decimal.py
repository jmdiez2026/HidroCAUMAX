"""Present native CAUMAX numeric edits with a decimal comma without changing its data.

CAUMAX 3.0 parses QLineEdit.text() with float() and has native textChanged
handlers which also parse the signal's *string* argument. Changing the native
widgets to comma text therefore breaks both calculations and dependent fields.
This UI-only editor delegates edits using canonical dot strings, so neither
CAUMAX files nor their widget values or signals are modified.
"""
import math
import re

from qgis.PyQt.QtCore import QEvent, QObject
from qgis.PyQt.QtWidgets import QLineEdit


# Only the rational dialog's native numeric fields. Do not attach to plugins'
# own widgets or to arbitrary QGIS text fields.
DECIMAL_FIELDS = (
    'txtX', 'txtY', 'txtArea', 'txtConcentrationTime', 'txtDistance',
    'txtHighest', 'txtLowest', 'txtRainfall', 'txtRainfallCorrection',
    'txtCorrectedRainfall', 'txtIntensityFactor', 'txtI1Id',
    'txtIntensity', 'txtCFactor', 'txtUniformity', 'txtP0',
    'txtP0Corrector', 'txtP0CorrectorReturnPeriod', 'txtCorrectedP0',
    'txtResultFlow', 'txtResultReturnPeriod',
)
_NUMERIC = re.compile(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z')


def decimal_display(text):
    """Only change dots in whole numeric strings, not arbitrary messages."""
    value = str(text)
    return value.replace('.', ',') if _NUMERIC.fullmatch(value) else value


def canonical_numeric(text):
    """Convert the Spanish decimal comma into CAUMAX's native float syntax."""
    return str(text).replace(',', '.')


class _FieldPresentation(QObject):
    def _display(self, text):
        # CAUMAX originally displays Q rounded to an integer. Maintain two
        # decimals in the visual output, including already populated fields.
        if self.native.objectName() == 'txtResultFlow' and _NUMERIC.fullmatch(str(text)):
            try:
                value = float(text)
                if math.isfinite(value):
                    return f'{value:.2f}'.replace('.', ',')
            except (TypeError, ValueError, OverflowError):
                pass
        return decimal_display(text)

    def __init__(self, native):
        super().__init__(native)
        self.native = native
        self._writing = False
        self.editor = QLineEdit(native)
        self.editor.setObjectName('hidrocaumax_decimal_' + native.objectName())
        self.editor.setFrame(False)
        self.editor.setTextMargins(0, 0, 0, 0)
        self.editor.setAlignment(native.alignment())
        self.editor.setFont(native.font())
        self.editor.setPalette(native.palette())
        self.editor.setReadOnly(native.isReadOnly())
        self.editor.setToolTip(native.toolTip())
        self.editor.setText(self._display(native.text()))
        self.editor.textChanged.connect(self._user_edited)
        native.textChanged.connect(self._native_changed)
        native.installEventFilter(self)
        self._fit()
        self.editor.show()
        self.editor.raise_()

    def _fit(self):
        native = self.native
        editor = self.editor
        # Leave the real QLineEdit's frame visible; cover only its text area.
        editor.setGeometry(3, 1, max(1, native.width() - 6), max(1, native.height() - 2))
        editor.setFont(native.font())
        editor.setAlignment(native.alignment())
        editor.setPalette(native.palette())
        editor.setReadOnly(native.isReadOnly())
        editor.setToolTip(native.toolTip())
        # The native parent's enabled state also disables the child editor.
        editor.raise_()

    def _native_changed(self, text):
        if self._writing:
            return
        localized = self._display(text)
        if self.editor.text() != localized:
            cursor = self.editor.cursorPosition()
            self.editor.blockSignals(True)
            try:
                self.editor.setText(localized)
                if self.editor.hasFocus():
                    self.editor.setCursorPosition(min(cursor, len(localized)))
            finally:
                self.editor.blockSignals(False)

    def _user_edited(self, text):
        localized = decimal_display(text)
        if localized != text:
            position = self.editor.cursorPosition()
            self.editor.blockSignals(True)
            try:
                self.editor.setText(localized)
                self.editor.setCursorPosition(min(position, len(localized)))
            finally:
                self.editor.blockSignals(False)
        canonical = canonical_numeric(localized)
        self._writing = True
        try:
            # CAUMAX's original textChanged callbacks get 14.75, not 14,75.
            self.native.setText(canonical)
        finally:
            self._writing = False
        # CAUMAX may sanitize an incomplete input (e.g. an invalid tc); its
        # authoritative widget must always win over the temporary editor.
        if self.native.text() != canonical:
            self._native_changed(self.native.text())

    def eventFilter(self, watched, event):
        if watched is self.native:
            if event.type() in (
                QEvent.Resize, QEvent.Show, QEvent.StyleChange,
                QEvent.FontChange, QEvent.PaletteChange, QEvent.EnabledChange,
            ):
                self._fit()
            elif event.type() == QEvent.FocusIn and self.native.isEnabled():
                self.editor.setFocus()
        return False

    def dispose(self):
        try:
            self.native.removeEventFilter(self)
            self.native.textChanged.disconnect(self._native_changed)
            self.editor.textChanged.disconnect(self._user_edited)
            self.editor.hide()
            self.editor.setParent(None)
            self.editor.deleteLater()
        except (AttributeError, RuntimeError, TypeError):
            pass


class RationalDecimalPresentation:
    """Reversible comma-decimal front end for one live native rational dialog."""
    def __init__(self, dialog):
        self.fields = []
        for name in DECIMAL_FIELDS:
            native = getattr(dialog, name, None)
            if isinstance(native, QLineEdit):
                self.fields.append(_FieldPresentation(native))

    def sync(self):
        """Refresh fields written with blockSignals(True) by original CAUMAX.

        Its calculator intentionally suppresses textChanged on I1/Id and I;
        the presentation must not infer that an unchanged signal means an
        unchanged numeric value.
        """
        for field in self.fields:
            try:
                field._native_changed(field.native.text())
                field._fit()
            except RuntimeError:
                continue

    def dispose(self):
        for field in reversed(self.fields):
            field.dispose()
        self.fields.clear()
