"""Prepare or verify the vendored TauDEM 5.5.0 Windows x64 runtime, offline.

TauDEM is the Terrain Analysis Using Digital Elevation Models project by
David G. Tarboton and contributors, Utah State University:
https://github.com/dtarb/TauDEM and https://hydrology.usu.edu/taudem/.
The stage must contain the unmodified official binaries, their dependencies,
projection data and original licenses. This tool does not download, execute,
install or license those components. See TAUDEM_BUNDLE.md beside this script.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import tempfile
import zipfile


RUNTIME_ID = 'taudem-5.5.0-win64'
IDENTITY = dict(schema_version=1, runtime_id=RUNTIME_ID, platform='windows',
                architecture='x86_64', taudem_version='5.5.0')
TIMESTAMP = (1980, 1, 1, 0, 0, 0)
REQUIRED_BINARIES = tuple('bin/'+name for name in (
    'PitRemove.exe', 'D8FlowDir.exe', 'AreaD8.exe', 'Threshold.exe',
    'StreamNet.exe', 'mpiexec.exe', 'smpd.exe', 'msmpi.dll'))
REQUIRED_FILES = REQUIRED_BINARIES + ('share/proj/proj.db',)
ROOTS = {'bin', 'share', 'licenses'}
TEXT_SUFFIXES = {'.txt', '.md', '.rst', '.json', '.xml', '.ini', '.cfg',
                 '.yaml', '.yml', '.csv', '.html', '.htm'}
HOME_PATH = re.compile(r'(?i)(?:[a-z]:[/\\]+(?:users|documents and settings)[/\\]+[^\s/\\]+|/(?:home|Users)/[^\s/]+)')
SECRET = re.compile(r'-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|'
                    r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|'
                    r'AKIA[A-Z0-9]{16}|sk-(?:proj-)?[A-Za-z0-9_-]{24,})\b')
PRIVATE_SUFFIXES = {'.key', '.pem', '.p12', '.pfx'}
WINDOWS_RESERVED = {'con', 'prn', 'aux', 'nul'} | {
    prefix+str(number) for prefix in ('com', 'lpt') for number in range(1, 10)}


def validate_name(name: str, *, manifest: bool = False) -> None:
    """Only portable, relative payload paths may enter a Windows archive."""
    if not isinstance(name, str):
        raise ValueError('Payload paths must be strings.')
    if manifest and name == 'manifest.json':
        return
    path = PurePosixPath(name)
    if (any(character in name for character in '\\:<>"|?*')
            or any(ord(character) < 32 for character in name)
            or path.is_absolute() or path.as_posix() != name
            or len(path.parts) < 2 or path.parts[0] not in ROOTS):
        raise ValueError('Unsafe or unexpected payload path: '+repr(name))
    for part in path.parts:
        if (part in ('.', '..') or part.startswith('.') or part.endswith((' ', '.'))
                or part.split('.')[0].lower() in WINDOWS_RESERVED):
            raise ValueError('Unsafe or hidden payload path: '+name)
    if (path.suffix.lower() in PRIVATE_SUFFIXES
            or path.name.lower().startswith(('credentials', 'secrets'))):
        raise ValueError('Credential material is not allowed: '+name)


def validate_content(name: str, data: bytes) -> None:
    # Scan documentation/configuration, not binary debug/source strings shipped
    # by upstream. Neither absolute staging paths nor local configuration belong
    # in the bundle. A scan cannot certify arbitrary binary contents as secret-free.
    if PurePosixPath(name).suffix.lower() in TEXT_SUFFIXES:
        text = data.decode('utf-8-sig', errors='replace')
        if HOME_PATH.search(text) or SECRET.search(text):
            raise ValueError('Personal path or credential pattern in: '+name)
    if name.lower() in {item.lower() for item in REQUIRED_BINARIES}:
        if len(data) < 64 or data[:2] != b'MZ':
            raise ValueError('Required binary is not a Windows PE executable: '+name)
        offset = struct.unpack_from('<I', data, 60)[0]
        if (offset + 6 > len(data) or data[offset:offset+4] != b'PE\0\0'
                or struct.unpack_from('<H', data, offset+4)[0] != 0x8664):
            raise ValueError('Required binary is not Windows x86_64: '+name)
    if name.lower() == 'share/proj/proj.db' and not data.startswith(b'SQLite format 3\0'):
        raise ValueError('PROJ database is not a SQLite database.')
    if name.startswith('licenses/') and not data.strip():
        raise ValueError('License files must not be empty: '+name)


def validate_inventory(names: list[str]) -> None:
    lowered = [name.lower() for name in names]
    if len(lowered) != len(set(lowered)):
        raise ValueError('Duplicate or case-colliding Windows payload paths.')
    missing = [name for name in REQUIRED_FILES if name.lower() not in lowered]
    if missing:
        raise ValueError('Missing required runtime files: '+', '.join(missing))
    if not any(name.startswith('licenses/') for name in names):
        raise ValueError('Original component licenses are required under licenses/.')


def _is_link(path: Path) -> bool:
    # A Windows junction/reparse point must not evade the normal symlink check.
    metadata = path.lstat()
    return path.is_symlink() or bool(getattr(metadata, 'st_file_attributes', 0)
                                    & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400))


def stage_files(stage: Path) -> dict[str, bytes]:
    if _is_link(stage) or not stage.is_dir():
        raise ValueError('Stage must be a real directory, without symbolic links.')
    root = stage.resolve()
    payload = {}
    for path in sorted(stage.rglob('*')):
        if _is_link(path):
            raise ValueError('Symbolic links and junctions are not allowed in the stage.')
        if not path.resolve().is_relative_to(root):
            raise ValueError('Stage member escaped the staging directory.')
        name = path.relative_to(stage).as_posix()
        if path.is_dir():
            if name.split('/')[0] not in ROOTS:
                raise ValueError('Stage may contain only bin/, share/ and licenses/.')
            continue
        validate_name(name)
        if not stat.S_ISREG(path.stat().st_mode):
            raise ValueError('Only regular files may enter the runtime bundle: '+name)
        data = path.read_bytes()
        if not data:
            raise ValueError('Empty runtime file: '+name)
        validate_content(name, data)
        payload[name] = data
    validate_inventory(list(payload))
    return payload


def manifest_for(payload: dict[str, bytes]) -> dict:
    return dict(IDENTITY, files={name: hashlib.sha256(data).hexdigest()
                                for name, data in sorted(payload.items())})


def validate_bundle(path: Path) -> dict:
    """Verify identity, safe paths, required components and every payload hash."""
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(name.lower() for name in names)):
            raise ValueError('Duplicate or case-colliding ZIP members.')
        for info in infos:
            validate_name(info.filename, manifest=True)
            if (info.is_dir() or stat.S_ISLNK(info.external_attr >> 16)
                    or info.flag_bits & 1 or info.date_time != TIMESTAMP
                    or info.compress_type != zipfile.ZIP_DEFLATED):
                raise ValueError('Unsafe or non-reproducible ZIP entry: '+info.filename)
        if 'manifest.json' not in names:
            raise ValueError('The runtime manifest.json is missing.')
        manifest = json.loads(archive.read('manifest.json').decode('utf-8'))
        if (not isinstance(manifest, dict) or set(manifest) != set(IDENTITY) | {'files'}
                or any(manifest.get(key) != value for key, value in IDENTITY.items())
                or not isinstance(manifest.get('files'), dict)):
            raise ValueError('Unexpected runtime manifest identity or schema.')
        hashes = manifest['files']
        if set(hashes) != set(names) - {'manifest.json'}:
            raise ValueError('Manifest file list does not match ZIP payload.')
        validate_inventory(list(hashes))
        for name, digest in hashes.items():
            data = archive.read(name)
            if (not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)
                    or hashlib.sha256(data).hexdigest() != digest):
                raise ValueError('Runtime payload SHA-256 mismatch: '+name)
            if not data:
                raise ValueError('Empty runtime file: '+name)
            validate_content(name, data)
    return manifest


def prepare(stage: Path, output: Path) -> dict:
    """Build atomically from a byte snapshot; never change the staged runtime."""
    stage, output = Path(stage), Path(output)
    if output.is_symlink() or (output.exists() and _is_link(output)):
        raise ValueError('Output must not be a symbolic link or junction.')
    if output.resolve().is_relative_to(stage.resolve()):
        raise ValueError('Output must be outside the staging directory.')
    payload = stage_files(stage)
    manifest = manifest_for(payload)
    payload['manifest.json'] = (json.dumps(manifest, ensure_ascii=False, indent=2,
                                          sort_keys=True)+'\n').encode('utf-8')
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix='.taudem-bundle-', suffix='.zip',
                                                dir=output.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED,
                             compresslevel=9) as archive:
            for name, data in sorted(payload.items()):
                info = zipfile.ZipInfo(name, date_time=TIMESTAMP)
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED,
                                 compresslevel=9)
        validate_bundle(temporary)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, help='Local bin/, share/, licenses/ staging directory.')
    parser.add_argument('--output', type=Path, help='Destination taudem-5.5.0-win64.zip.')
    parser.add_argument('--verify', type=Path, help='Verify an existing vendored ZIP; write nothing.')
    args = parser.parse_args()
    if args.verify:
        if args.stage or args.output:
            parser.error('--verify cannot be combined with --stage or --output.')
        manifest = validate_bundle(args.verify)
        path = args.verify
    else:
        if not args.stage or not args.output:
            parser.error('Both --stage and --output are required when building.')
        manifest = prepare(args.stage, args.output)
        path = args.output
    print(json.dumps(dict(runtime_id=manifest['runtime_id'], files=len(manifest['files']),
                          bytes=path.stat().st_size,
                          sha256=hashlib.sha256(path.read_bytes()).hexdigest()), indent=2))


if __name__ == '__main__':
    main()
