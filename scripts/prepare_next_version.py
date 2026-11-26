"""Prepare the next stable QGIS plugin version, without creating a release.

GitHub Actions calls this before tagging and dispatching release.yml.
The folder id ``caumax_consulta`` is never changed. Re-running with the same
version is safe when a previous workflow reached the commit/tag phase.
"""
from __future__ import annotations

import argparse
import configparser
import json
from pathlib import Path
import re


VERSION_RE = re.compile(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z')
PLUGIN = Path('src/caumax_consulta')


def version_parts(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or len(value) > 32 or not VERSION_RE.fullmatch(value):
        raise ValueError('Especifica una versión estable de tres componentes, p. ej. 1.0.1.')
    return tuple(map(int, value.split('.')))


def public_tag(value: str) -> str:
    version_parts(value)
    return 'v1.0' if value == '1.0.0' else 'v' + value


def _one(content: str, before: str, after: str, source: str) -> str:
    if content.count(before) != 1:
        raise ValueError(f'{source}: no hay una única referencia esperada de versión: {before!r}')
    return content.replace(before, after, 1)


def _many(content: str, before: str, after: str, source: str) -> str:
    if before not in content:
        raise ValueError(f'{source}: no se encontró la referencia esperada: {before!r}')
    return content.replace(before, after)


def plan(root: Path, target: str) -> dict[Path, bytes]:
    """Validate every expected source before returning any changed bytes."""
    target_parts = version_parts(target)
    root = Path(root).resolve()
    metadata_path = root / PLUGIN / 'metadata.txt'
    metadata_raw = metadata_path.read_bytes()
    document = configparser.ConfigParser(interpolation=None)
    document.read_string(metadata_raw.decode('utf-8-sig'))
    existing = document.get('general', 'version')
    current_parts = version_parts(existing)
    if target_parts < current_parts:
        raise ValueError(f'No se puede retroceder de {existing} a {target}.')
    current_name = document.get('general', 'name')
    family_name = lambda version: 'HidroCAUMAX v.' + '.'.join(version.split('.')[:2])
    family = current_name == family_name(existing)
    if current_name not in ('HidroCAUMAX ' + public_tag(existing), family_name(existing)):
        raise ValueError('El nombre visible y la versión instalada son incoherentes.')
    next_name = family_name(target) if family else 'HidroCAUMAX ' + public_tag(target)
    if target_parts == current_parts:
        if target == '1.0.0':
            raise ValueError('v1.0 ya está publicada. La próxima versión debe ser 1.0.1 o superior.')
        return {}  # Reintento seguro: la publicación puede haberse interrumpido.

    old_tag, next_tag = public_tag(existing), public_tag(target)
    old_zip = 'HidroCAUMAX_v' + existing.replace('.', '_')
    next_zip = 'HidroCAUMAX_v' + target.replace('.', '_')
    files: dict[Path, str] = {}

    def source(relative: Path | str) -> tuple[Path, str]:
        path = root / relative
        content = path.read_bytes().decode('utf-8')  # Preserve CRLF bytes in generated edits.
        return path, content

    path, content = source(PLUGIN / 'metadata.txt')
    content = _one(content, '\nname=' + current_name + '\n',
                   '\nname=' + next_name + '\n', str(path))
    content = _one(content, '\nversion=' + existing + '\n',
                   '\nversion=' + target + '\n', str(path))
    files[path] = content

    for filename in (('basin_dialog.py', 'reports.py', 'rational_export.py', 'native_plugin.py') if family
                     else ('basin_dialog.py', 'reports.py', 'rational_export.py')):
        path, content = source(PLUGIN / filename)
        files[path] = _many(content, current_name, next_name, filename)

    path, content = source(PLUGIN / 'ai_analysis.py')
    files[path] = _one(content, "b'HidroCAUMAX/" + old_tag[1:] + "'",
                       "b'HidroCAUMAX/" + next_tag[1:] + "'", str(path))

    path, content = source('README.md')
    content = _one(content,
        f'Versión pública actual: **{old_tag}** (versión técnica del paquete: **{existing}**)',
        f'Versión pública actual: **{next_tag}** (versión técnica del paquete: **{target}**)', str(path))
    content = _one(content, f'Descargas de la versión **{old_tag}**:',
                   f'Descargas de la versión **{next_tag}**:', str(path))
    content = _one(content, f'La versión {old_tag} se distribuye',
                   f'La versión {next_tag} se distribuye', str(path))
    content = _many(content, f'/releases/download/{old_tag}/',
                    f'/releases/download/{next_tag}/', str(path))
    content = _many(content, old_zip, next_zip, str(path))
    content = _many(content, f'--tag {old_tag}', f'--tag {next_tag}', str(path))
    files[path] = content

    path, content = source(PLUGIN / 'README.md')
    content = _one(content, '# ' + current_name + '\n', '# ' + next_name + '\n', str(path))
    if family:
        content = _one(content,
            f'La versión técnica del paquete es **{existing}** y la identificación visible es **{current_name}**',
            f'La versión técnica del paquete es **{target}** y la identificación visible es **{next_name}**', str(path))
    else:
        content = _one(content,
            f'La versión técnica del paquete es **{existing}** y la identificación pública y del diálogo es **{old_tag}**',
            f'La versión técnica del paquete es **{target}** y la identificación pública y del diálogo es **{next_tag}**', str(path))
    content = _many(content, old_zip, next_zip, str(path))
    content = _many(content, f'/releases/tag/{old_tag}',
                    f'/releases/tag/{next_tag}', str(path))
    content = _many(content, f'El nombre visible es **{current_name}**',
                    f'El nombre visible es **{next_name}**', str(path))
    files[path] = content
    return {path: content.encode('utf-8') for path, content in files.items()
            if path.read_bytes() != content.encode('utf-8')}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True, help='Versión técnica futura, ej. 1.0.1')
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--check', action='store_true', help='Validar sin modificar archivos')
    args = parser.parse_args(argv)
    try:
        changes = plan(args.root, args.version)
        if not args.check:
            for path, body in changes.items():
                path.write_bytes(body)
    except (ValueError, OSError, configparser.Error) as error:
        parser.error(str(error))
    print(json.dumps(dict(version=args.version, tag=public_tag(args.version),
        changed=[str(path.relative_to(args.root.resolve())) for path in changes],
        dry_run=args.check), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
