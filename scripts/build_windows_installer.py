"""Compile and inspect the per-user Windows installer from an official ZIP.

Requires NSIS 3 (makensis) and 7-Zip (7z or 7zz) on the build host; neither is
needed on the Windows computer. No Windows execution is implied by these checks.
"""
from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile


DEFAULT_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ID = 'caumax_consulta'
PROOF_NAME = 'HidroCAUMAX_payload_manifest.json'
VERSION_RE = re.compile(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z')
BUILD_SOURCES = ('scripts/build_windows_installer.py', 'packaging/windows/installer.nsi',
                 'packaging/windows/hidrocaumax.ico')
PROFILE_ROOT = r'%APPDATA%\QGIS\QGIS3\profiles'


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _document(value):
    return value if isinstance(value, dict) else json.loads(Path(value).read_text(encoding='utf-8'))


def _safe_relative(name):
    parts = PurePosixPath(name).parts
    if (not parts or name.startswith('/') or '\\' in name or '..' in parts
            or '/'.join(parts) != name):
        raise ValueError('Unsafe payload path: ' + name)
    for part in parts:
        if (any(ord(char) < 32 or char in '<>:"|?*' for char in part)
                or part.endswith((' ', '.'))
                or part.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL',
                    *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}):
            raise ValueError('Payload path is not safe on Windows: ' + name)
    return parts


def validate_payload(zip_path, release_manifest):
    """Require every ZIP byte and member to agree with the release manifest."""
    path = Path(zip_path)
    manifest = _document(release_manifest)
    version = manifest.get('version', '')
    if not VERSION_RE.fullmatch(version) or manifest.get('plugin_id') != PLUGIN_ID:
        raise ValueError('Invalid plugin/version in release manifest.')
    if (manifest.get('package') != path.name or manifest.get('sha256') != sha256(path)
            or manifest.get('bytes') != path.stat().st_size):
        raise ValueError('The ZIP does not match its release manifest.')
    files = manifest.get('source_files')
    if not isinstance(files, dict) or not files:
        raise ValueError('The release manifest has no source file hashes.')
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(names) != len(set(name.casefold() for name in names)) or set(names) != set(files):
            raise ValueError('ZIP members differ from the manifest or collide on Windows.')
        for entry in entries:
            parts = _safe_relative(entry.filename)
            if (parts[0] != PLUGIN_ID or len(parts) < 2 or entry.is_dir()
                    or stat.S_ISLNK(entry.external_attr >> 16)):
                raise ValueError('Unexpected payload member: ' + entry.filename)
            if hashlib.sha256(archive.read(entry)).hexdigest() != files[entry.filename]:
                raise ValueError('Payload hash mismatch: ' + entry.filename)
        metadata = configparser.ConfigParser(interpolation=None)
        metadata.read_string(archive.read(PLUGIN_ID + '/metadata.txt').decode('utf-8-sig'))
        if metadata.get('general', 'version') != version:
            raise ValueError('The plugin metadata version differs from the release manifest.')
        for name, value in (('qgisMinimumVersion', '3.34'), ('qgisMaximumVersion', '3.99')):
            if metadata.get('general', name) != value:
                raise ValueError('Unexpected QGIS compatibility: ' + name)
    return manifest


def _quote(value):
    value = str(value)
    if any(ord(char) < 32 for char in value):
        raise ValueError('Control characters cannot appear in NSIS strings.')
    return value.replace('$', '$$').replace('"', '$\\"')


def _compile_quote(value):
    """Compiler input filenames do not interpret runtime dollar variables."""
    value = str(value)
    if any(ord(char) < 32 for char in value):
        raise ValueError('Control characters cannot appear in NSIS filenames.')
    return value.replace('"', '$\\"')


def _generate_payload_include(payload_root, names):
    install, delete, directories = [], [], set()
    current = None
    for name in sorted(names):
        relative = PurePosixPath(name).relative_to(PLUGIN_ID)
        folder = relative.parent.as_posix()
        if folder != current:
            suffix = '' if folder == '.' else '\\' + folder.replace('/', '\\')
            install.append('  SetOutPath "${TARGET}' + _quote(suffix) + '"')
            current = folder
        install.append('  File "/oname=' + _quote(relative.name) + '" "'
                       + _compile_quote(Path(payload_root) / relative) + '"')
        destination = _quote(relative.as_posix().replace('/', '\\'))
        if relative.as_posix() != 'metadata.txt':
            delete += ['  IfFileExists "${TARGET}\\' + destination + '" 0 +2',
                       '    Delete "${TARGET}\\' + destination + '"']
        for parent in relative.parents:
            if str(parent) != '.':
                directories.add(parent.as_posix())
    empty = ['  RMDir "${TARGET}\\' + _quote(name.replace('/', '\\')) + '"'
             for name in sorted(directories, key=lambda item: (-item.count('/'), item))]
    return ('!macro HCAU_INSTALL_PAYLOAD TARGET\n' + '\n'.join(install)
            + '\n!macroend\n!macro HCAU_DELETE_PAYLOAD TARGET\n' + '\n'.join(delete)
            + '\n!macroend\n!macro HCAU_DELETE_METADATA TARGET\n'
              '  IfFileExists "${TARGET}\\metadata.txt" 0 +2\n'
              '    Delete "${TARGET}\\metadata.txt"'
            + '\n!macroend\n!macro HCAU_REMOVE_EMPTY_DIRS TARGET\n' + '\n'.join(empty) + '\n!macroend\n')


def _tool(name, supplied=None):
    result = str(supplied) if supplied is not None else shutil.which(name)
    if not result or not Path(result).is_file():
        raise RuntimeError(f'{name} is required. Install NSIS and 7-Zip on the build host (apt-get install nsis p7zip-full).')
    return str(Path(result).resolve())


def _extractor(supplied=None):
    return _tool('7z', supplied or shutil.which('7z') or shutil.which('7zz'))


def _icon_images(path):
    data = Path(path).read_bytes()
    if len(data) < 6 or struct.unpack_from('<HH', data) != (0, 1):
        raise ValueError('The installer icon must be an ICO file.')
    count = struct.unpack_from('<H', data, 4)[0]
    if not count or len(data) < 6 + count * 16:
        raise ValueError('Truncated ICO directory.')
    images = []
    for index in range(count):
        size, offset = struct.unpack_from('<II', data, 6 + index * 16 + 8)
        if not size or offset < 6 + count * 16 or offset + size > len(data):
            raise ValueError('Truncated ICO image.')
        images.append(data[offset:offset + size])
    return images


def _pe_resources(path):
    """Read PE resource data without loading or executing the Windows binary."""
    data = Path(path).read_bytes()
    if len(data) < 64 or data[:2] != b'MZ':
        raise ValueError('The installer is not a Windows executable.')
    offset = struct.unpack_from('<I', data, 60)[0]
    if offset + 24 > len(data) or data[offset:offset + 4] != b'PE\0\0':
        raise ValueError('Invalid PE signature.')
    machine, sections = struct.unpack_from('<HH', data, offset + 4)
    optional_size = struct.unpack_from('<H', data, offset + 20)[0]
    optional = offset + 24
    magic = struct.unpack_from('<H', data, optional)[0]
    if machine != 0x14C or magic != 0x10B or struct.unpack_from('<H', data, optional + 68)[0] != 2:
        raise ValueError('Expected the Unicode NSIS x86 GUI bootstrap (compatible with x64 Windows).')
    header = optional + optional_size
    def file_offset(rva):
        for index in range(sections):
            entry = header + index * 40
            virtual_size, address, raw_size, raw = struct.unpack_from('<IIII', data, entry + 8)
            if address <= rva < address + max(virtual_size, raw_size):
                return raw + rva - address
        raise ValueError('A PE resource RVA is not mapped.')
    resource_rva = struct.unpack_from('<I', data, optional + 96 + 16)[0]
    base = file_offset(resource_rva)
    resources = {}
    def visit(relative, resource_type=None, depth=0):
        if depth > 4:
            raise ValueError('Invalid PE resource tree.')
        position = base + relative
        named, numbered = struct.unpack_from('<HH', data, position + 12)
        for index in range(named + numbered):
            name, target = struct.unpack_from('<II', data, position + 16 + 8 * index)
            kind = (name if not name & 0x80000000 else None) if resource_type is None else resource_type
            if target & 0x80000000:
                visit(target & 0x7FFFFFFF, kind, depth + 1)
            else:
                rva, size = struct.unpack_from('<II', data, base + target)
                start = file_offset(rva)
                if start + size > len(data):
                    raise ValueError('Truncated PE resource.')
                resources.setdefault(kind, []).append(data[start:start + size])
    visit(0)
    manifests = resources.get(24, [])
    levels = []
    for resource in manifests:
        xml = ET.fromstring(resource.rstrip(b'\x00'))
        levels.extend(item.get('level') for item in xml.iter() if item.tag.endswith('requestedExecutionLevel'))
    if levels != ['asInvoker']:
        raise ValueError('The Windows executable must request asInvoker, never elevation.')
    return resources


def verify_installer(exe_path, installer_manifest, zip_path, release_manifest, *, source_root=None, extractor=None):
    """Verify build provenance and every extracted plugin byte; never execute PE."""
    exe = Path(exe_path)
    manifest, release = _document(installer_manifest), validate_payload(zip_path, release_manifest)
    root = DEFAULT_ROOT if source_root is None else Path(source_root)
    required = dict(schema_version=1, version=release['version'], plugin_id=PLUGIN_ID,
                    installer=exe.name, sha256=sha256(exe), bytes=exe.stat().st_size,
                    payload_zip=Path(zip_path).name, payload_sha256=release['sha256'],
                    payload_bytes=release['bytes'], payload_source_files=release['source_files'],
                    requires_admin=False, profile_root=PROFILE_ROOT)
    for key, value in required.items():
        if manifest.get(key) != value:
            raise ValueError('Installer manifest mismatch: ' + key)
    sources = manifest.get('build_sources')
    if not isinstance(sources, dict) or set(sources) != set(BUILD_SOURCES):
        raise ValueError('Installer build_sources must identify the builder, template and icon.')
    for name, digest in sources.items():
        _safe_relative(name)
        path = root / name
        if path.is_symlink() or sha256(path) != digest:
            raise ValueError('Installer source mismatch: ' + name)
    if (manifest.get('script_sha256') != sources['packaging/windows/installer.nsi']
            or manifest.get('icon_sha256') != sources['packaging/windows/hidrocaumax.ico']):
        raise ValueError('Installer template/icon hashes are inconsistent.')
    resources = _pe_resources(exe)
    icons = resources.get(3, [])
    if not all(image in icons for image in _icon_images(root / 'packaging/windows/hidrocaumax.ico')):
        raise ValueError('The EXE does not contain the versioned installer icon images.')
    with tempfile.TemporaryDirectory(prefix='hidrocaumax_exe_verify_') as temp:
        result = subprocess.run([_extractor(extractor), 'x', '-y', '-bd', '-o' + temp, str(exe.resolve())],
                                capture_output=True, text=True, timeout=180)
        if result.returncode:
            raise ValueError('7-Zip could not extract the installer: ' + result.stderr[-2000:])
        proofs = list(Path(temp).rglob(PROOF_NAME))
        if len(proofs) != 1:
            raise ValueError('The installer must contain exactly one payload proof.')
        proof = json.loads(proofs[0].read_text(encoding='utf-8'))
        if proof != {'plugin_id': PLUGIN_ID, 'version': release['version'],
                     'zip_sha256': release['sha256'], 'source_files': release['source_files']}:
            raise ValueError('Embedded payload proof differs from the official ZIP.')
        payload = proofs[0].parent
        expected = {name.removeprefix(PLUGIN_ID + '/'): digest for name, digest in release['source_files'].items()}
        allowed = set(expected) | {PROOF_NAME, 'uninstall-HidroCAUMAX.exe'}
        actual = {path.relative_to(payload).as_posix() for path in payload.rglob('*') if path.is_file()}
        if not set(expected) <= actual or actual - allowed:
            raise ValueError('Extracted payload has missing or unexpected files.')
        for name, digest in expected.items():
            path = payload / name
            if path.is_symlink() or sha256(path) != digest:
                raise ValueError('The EXE payload differs from the ZIP: ' + name)
    return {'verified': True, 'payload_files': len(release['source_files']), 'execution_level': 'asInvoker',
            'windows_executed': False}


def build(zip_path, release_manifest, output_dir, *, icon=None, makensis=None, extractor=None, source_root=None):
    root = DEFAULT_ROOT if source_root is None else Path(source_root)
    zip_path = Path(zip_path).resolve()
    release = validate_payload(zip_path, release_manifest)
    icon = root / 'packaging/windows/hidrocaumax.ico' if icon is None else Path(icon)
    if sha256(icon) != sha256(root / 'packaging/windows/hidrocaumax.ico'):
        raise ValueError('The selected icon must match the versioned packaging/windows/hidrocaumax.ico.')
    _icon_images(icon)
    compiler = _tool('makensis', makensis)
    extract = _extractor(extractor)
    environment = dict(os.environ)
    nsis_directory = Path(compiler).parent.parent / 'share/nsis'
    if nsis_directory.is_dir():
        environment.setdefault('NSISDIR', str(nsis_directory))
    version = subprocess.run([compiler, '-VERSION'], capture_output=True, text=True,
                             check=True, env=environment).stdout.strip()
    if not re.search(r'v?3\.', version):
        raise ValueError('NSIS 3 Unicode is required.')
    output = Path(output_dir).resolve(); output.mkdir(parents=True, exist_ok=True)
    name = 'HidroCAUMAX_v' + release['version'].replace('.', '_') + '_Setup.exe'
    target = output / name
    sources = {path: sha256(root / path) for path in BUILD_SOURCES}
    with tempfile.TemporaryDirectory(prefix='hidrocaumax_nsis_') as temp:
        work = Path(temp); payload = work / 'payload'; payload.mkdir()
        with zipfile.ZipFile(zip_path) as archive:
            for name_in_zip in release['source_files']:
                path = payload / PurePosixPath(name_in_zip).relative_to(PLUGIN_ID)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(archive.read(name_in_zip))
        include = work / 'payload.nsh'
        include.write_text(_generate_payload_include(payload, release['source_files']), encoding='utf-8')
        proof = work / PROOF_NAME
        proof.write_text(json.dumps({'plugin_id': PLUGIN_ID, 'version': release['version'],
            'zip_sha256': release['sha256'], 'source_files': release['source_files']},
            ensure_ascii=False, sort_keys=True, separators=(',', ':')), encoding='utf-8')
        staging_exe = work / target.name
        defines = {'PAYLOAD_INCLUDE': include, 'HCAU_VERSION': release['version'],
            'HCAU_OUTPUT': staging_exe, 'HCAU_ICON': icon.resolve(), 'HCAU_PROOF': proof,
            'HCAU_PAYLOAD_SHA256': release['sha256'], 'HCAU_QGIS_MIN': '3.34', 'HCAU_QGIS_MAX': '3.99',
            'HCAU_ESTIMATED_KB': math.ceil(sum(path.stat().st_size for path in payload.rglob('*') if path.is_file()) / 1024)}
        script = work / 'build.nsi'
        script.write_text('\n'.join('!define ' + key + ' "' + _compile_quote(value) + '"' for key, value in defines.items())
                          + '\n!include "' + _compile_quote(root / 'packaging/windows/installer.nsi') + '"\n', encoding='utf-8')
        compiled = subprocess.run([compiler, '-V3', '-INPUTCHARSET', 'UTF8', str(script)], env=environment,
                                  capture_output=True, text=True, timeout=300)
        if compiled.returncode:
            raise RuntimeError('NSIS compilation failed:\n' + compiled.stdout[-10000:] + compiled.stderr[-10000:])
        manifest = dict(schema_version=1, version=release['version'], plugin_id=PLUGIN_ID,
            installer=target.name, sha256=sha256(staging_exe), bytes=staging_exe.stat().st_size,
            payload_zip=zip_path.name, payload_sha256=release['sha256'], payload_bytes=release['bytes'],
            payload_source_files=release['source_files'], nsis_version=version,
            icon_sha256=sources['packaging/windows/hidrocaumax.ico'],
            script_sha256=sources['packaging/windows/installer.nsi'], build_sources=sources,
            requires_admin=False, profile_root=PROFILE_ROOT, windows_executed=False,
            validation='PE resources and complete extracted payload verified; Windows execution not performed')
        verify_installer(staging_exe, manifest, zip_path, release, source_root=root, extractor=extract)
        shutil.copyfile(staging_exe, target)
    manifest_path = output / 'installer_manifest.json'
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--zip', required=True, type=Path, dest='zip_path')
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--icon', type=Path)
    parser.add_argument('--makensis', type=Path)
    parser.add_argument('--extractor', type=Path)
    args = parser.parse_args()
    result = build(args.zip_path, args.manifest, args.output_dir, icon=args.icon,
                   makensis=args.makensis, extractor=args.extractor)
    print(json.dumps({key: value for key, value in result.items() if key not in ('payload_source_files', 'build_sources')},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
