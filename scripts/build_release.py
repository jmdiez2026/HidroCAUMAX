"""Build and verify a reproducible QGIS plugin ZIP and its public repository index.

Uses only Python's standard library. It neither changes plugin sources nor
publishes anything. Archive entries keep the installed id ``caumax_consulta``.
"""
from __future__ import annotations

import argparse
import ast
import configparser
import hashlib
import html
import json
import re
from pathlib import Path, PurePosixPath
import shutil
import stat
import xml.etree.ElementTree as ET
import zipfile

PLUGIN_ID = 'caumax_consulta'
DEFAULT_REPOSITORY = 'jmdiez2026/HidroCAUMAX'
DEFAULT_ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {'__pycache__', '.git', '.pytest_cache', '.mypy_cache'}
EXCLUDED_NAMES = {'.DS_Store', 'Thumbs.db'}
VERSION_RE = re.compile(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z')
REPOSITORY_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9_.-]*\Z')


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def validate_repository(repository: str) -> str:
    if not REPOSITORY_RE.fullmatch(repository) or '..' in repository:
        raise ValueError('Repository must be a GitHub owner/name, without URL or traversal.')
    return repository


def read_metadata(source: Path) -> dict[str, str]:
    config = configparser.ConfigParser(interpolation=None, strict=True)
    config.read(source/'metadata.txt', encoding='utf-8-sig')
    if not config.has_section('general'):
        raise ValueError('metadata.txt requires a [general] section.')
    metadata = dict(config['general'])
    for key in ('name', 'description', 'version', 'author'):
        if not metadata.get(key, '').strip():
            raise ValueError('Missing plugin metadata: '+key)
    if not VERSION_RE.fullmatch(metadata['version']):
        raise ValueError('Plugin version must be a plain major.minor.patch.')
    for key, expected in (('qgisminimumversion', '3.34'), ('qgismaximumversion', '3.99')):
        if metadata.get(key) != expected:
            raise ValueError(f'{key} must remain {expected}.')
    if metadata.get('experimental') not in ('True', 'False'):
        raise ValueError('experimental must explicitly be True or False.')
    return metadata


def source_files(source: Path) -> dict[str, Path]:
    if source.is_symlink() or source.name != PLUGIN_ID or not source.is_dir():
        raise ValueError('Source must be the real caumax_consulta directory.')
    root = source.resolve()
    result = {}
    for path in sorted(source.rglob('*')):
        relative = path.relative_to(source)
        if any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError('Symbolic links are not allowed in a plugin package: '+str(relative))
        if not path.is_file() or path.name in EXCLUDED_NAMES or path.suffix in ('.pyc', '.pyo'):
            continue
        if not path.resolve().is_relative_to(root):
            raise ValueError('Plugin source escaped the package directory.')
        if any(part.startswith('.') for part in relative.parts):
            raise ValueError('Unexpected hidden file in plugin source: '+str(relative))
        name = PLUGIN_ID+'/'+relative.as_posix()
        if path.suffix == '.py':
            ast.parse(path.read_text(encoding='utf-8-sig'), filename=relative.as_posix())
        result[name] = path
    if not {PLUGIN_ID+'/metadata.txt', PLUGIN_ID+'/__init__.py'} <= result.keys():
        raise ValueError('Plugin entry point and metadata.txt are required.')
    return result


def validate_zip(path: Path, files: dict[str, Path]) -> None:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError('ZIP contains duplicate member names.')
        for member in archive.infolist():
            name = member.filename
            parts = PurePosixPath(name).parts
            if ('\\' in name or ':' in name or name.startswith('/') or '..' in parts
                    or not parts or parts[0] != PLUGIN_ID or member.is_dir()
                    or stat.S_ISLNK(member.external_attr >> 16)):
                raise ValueError('Unsafe or unexpected ZIP member: '+name)
        if set(names) != set(files):
            raise ValueError('ZIP file list does not match the plugin source exactly.')
        if archive.testzip() is not None:
            raise ValueError('ZIP CRC validation failed.')
        for name, source in files.items():
            if archive.getinfo(name).file_size != source.stat().st_size:
                raise ValueError('ZIP member size differs from source: '+name)
            if archive.read(name) != source.read_bytes():
                raise ValueError('ZIP content differs from plugin source: '+name)


def write_zip(path: Path, files: dict[str, Path]) -> None:
    # Stable order, timestamp and permissions make repeated builds reproducible.
    # ZIP_STORED also avoids differing deflate streams across Windows/Linux
    # zlib versions; this small plugin stays byte-identical on both platforms.
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name, source in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, source.read_bytes(), compress_type=zipfile.ZIP_STORED)


def repository_xml(metadata: dict[str, str], repository: str, tag: str, filename: str) -> bytes:
    base = 'https://github.com/'+repository
    root = ET.Element('plugins')
    plugin = ET.SubElement(root, 'pyqgis_plugin', name=metadata['name'], version=metadata['version'])
    values = {'description': metadata['description'], 'about': metadata.get('about', metadata['description']),
        'version': metadata['version'], 'qgis_minimum_version': '3.34', 'qgis_maximum_version': '3.99',
        'homepage': base, 'repository': base, 'tracker': base+'/issues', 'author_name': metadata['author'],
        'email': metadata.get('email', ''), 'download_url': f'{base}/releases/download/{tag}/{filename}',
        # QGIS 3.44 derives the package id from file_name.partition('.')[0].
        # Keep this stable even though the GitHub asset has a versioned name.
        'file_name': PLUGIN_ID+'.zip', 'experimental': metadata['experimental'], 'deprecated': 'False',
        'category': metadata.get('category', 'Raster'), 'tags': 'hidrologia,cuencas,caumax'}
    for key, value in values.items():
        ET.SubElement(plugin, key).text = value
    ET.indent(root, space='  ')
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)+b'\n'


def build(source: Path, output: Path, tag: str | None = None,
          repository: str = DEFAULT_REPOSITORY, reuse_zip: Path | None = None) -> dict:
    repository = validate_repository(repository)
    if source.is_symlink():
        raise ValueError('Plugin source directory must not be a symbolic link.')
    source, output = source.resolve(), output.resolve()
    if output == source or output.is_relative_to(source):
        raise ValueError('Build output must be outside plugin source.')
    metadata = read_metadata(source)
    version = metadata['version']
    expected_tag = 'v'+version
    if tag is not None and tag != expected_tag:
        raise ValueError(f'Tag {tag!r} does not match metadata version {expected_tag}.')
    tag = expected_tag
    files = source_files(source)
    filename = 'HidroCAUMAX_v'+version.replace('.', '_')+'.zip'
    output.mkdir(parents=True, exist_ok=True)
    package = output/filename
    if reuse_zip:
        validate_zip(reuse_zip, files)
        if reuse_zip.resolve() != package:
            shutil.copyfile(reuse_zip, package)
    else:
        write_zip(package, files)
    validate_zip(package, files)
    xml = repository_xml(metadata, repository, tag, filename)
    parsed = ET.fromstring(xml)
    plugin = parsed.find('pyqgis_plugin')
    assert plugin.findtext('file_name').partition('.')[0] == PLUGIN_ID
    (output/'plugins.xml').write_bytes(xml)
    checksums = ''.join(f'{sha256(output/name)}  {name}\n' for name in (filename, 'plugins.xml'))
    (output/'SHA256SUMS.txt').write_text(checksums, encoding='ascii', newline='\n')
    owner, repo_name = repository.split('/')
    index_url = f'https://{owner}.github.io/{repo_name}/plugins.xml'
    manifest = dict(version=version, tag=tag, plugin_id=PLUGIN_ID,
        display_name=metadata['name'], repository=repository, qgis_minimum_version='3.34',
        qgis_maximum_version='3.99', experimental=metadata['experimental']=='True', package=filename,
        sha256=sha256(package), bytes=package.stat().st_size,
        index_url=index_url, download_url=plugin.findtext('download_url'),
        source_files={name:sha256(path) for name,path in sorted(files.items())})
    (output/'release_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    channel = 'experimental' if manifest['experimental'] else 'estable'
    changelog = source/'CHANGELOG.md'
    changes = changelog.read_text(encoding='utf-8') if changelog.is_file() else ''
    notes = (f'HidroCAUMAX {version} para QGIS 3.34–3.99.\n\n'
        f'Canal {channel}; requiere CAUMAX original.\n\n'
        + (changes.strip()+'\n\n' if changes else '') +
        f'Repositorio QGIS: {index_url}\n\n'
        f'Instalación manual: {filename}, sin descomprimir.\n\n'
        f'SHA-256: {manifest["sha256"]}\n')
    (output/'release_notes.md').write_text(notes, encoding='utf-8', newline='\n')
    site = output/'site'
    site.mkdir(exist_ok=True)
    (site/'plugins.xml').write_bytes(xml)
    (site/'.nojekyll').write_bytes(b'')
    (site/'SHA256SUMS.txt').write_text(checksums, encoding='ascii', newline='\n')
    page = f'''<!doctype html>
<html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>HidroCAUMAX — repositorio QGIS</title>
<main style="max-width:52rem;margin:3rem auto;padding:0 1rem;font:1.1rem/1.6 system-ui">
<h1>HidroCAUMAX {html.escape(version)}</h1>
<p>Canal {channel} para QGIS 3.34–3.99. Requiere CAUMAX original.</p>
<p>Añade este repositorio desde Complementos → Administrar e instalar complementos → Configuración:</p>
<p><a href="plugins.xml">{html.escape(index_url)}</a></p>
{'<p>Activa «Mostrar también los complementos experimentales» para que aparezca.</p>' if manifest['experimental'] else '<p>Con el repositorio habilitado, QGIS puede comprobar las nuevas versiones del complemento.</p>'}
<p><a href="{html.escape(manifest['download_url'],quote=True)}">Descargar ZIP instalable</a> ·
<a href="https://github.com/{html.escape(repository,quote=True)}">Código y documentación</a></p>
<p>Identificador del complemento: <code>caumax_consulta</code>.</p>
<p>SHA-256 del ZIP: <code style="overflow-wrap:anywhere">{manifest['sha256']}</code></p>
</main></html>
'''
    (site/'index.html').write_text(page,encoding='utf-8',newline='\n')
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=DEFAULT_ROOT/'src'/PLUGIN_ID)
    parser.add_argument('--output',type=Path,default=DEFAULT_ROOT/'dist')
    parser.add_argument('--tag',help='Must equal v plus metadata.txt version.')
    parser.add_argument('--repository',default=DEFAULT_REPOSITORY)
    parser.add_argument('--reuse-zip',type=Path,help='Preserve an existing ZIP only if every source byte matches.')
    args = parser.parse_args()
    manifest = build(args.source,args.output,args.tag,args.repository,args.reuse_zip)
    print(json.dumps({k:v for k,v in manifest.items() if k!='source_files'},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
