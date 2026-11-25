"""Publish/verify the prepared bundle with an already authenticated GitHub CLI.

Never replaces an existing asset. A retry validates its bytes before uploading
only missing files, then leaves Pages deployment to the workflow. Use --dry-run
to inspect the local bundle.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
from build_release import DEFAULT_REPOSITORY, DEFAULT_ROOT, PLUGIN_ID, build, sha256, validate_repository


def run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(args,cwd=DEFAULT_ROOT,text=True,encoding='utf-8',capture_output=True)
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or 'Command failed: '+args[0])
    return result


def api(endpoint: str, *, missing_ok: bool = False) -> dict | None:
    result=run(['gh','api',endpoint],check=False)
    if result.returncode:
        if missing_ok and 'HTTP 404' in result.stderr:
            return None
        raise RuntimeError(result.stderr.strip() or 'GitHub API failed.')
    return json.loads(result.stdout)


def _bundle_file(output: Path, name: str) -> Path:
    if (not isinstance(name,str) or not name or Path(name).name!=name
            or '\\' in name or ':' in name or name in ('.','..')):
        raise ValueError('Release asset must be a plain filename.')
    return output/name


def validate_bundle(output: Path, repository: str, installer: Path | None=None,
                    installer_manifest: Path | None=None) -> tuple[dict,list[Path]]:
    if (installer is None)!=(installer_manifest is None):
        raise ValueError('Provide both --installer and --installer-manifest, or neither.')
    manifest=json.loads((output/'release_manifest.json').read_text(encoding='utf-8'))
    if manifest['repository'] != validate_repository(repository):
        raise ValueError('Release manifest targets a different repository.')
    windows=manifest.get('windows_installer')
    arguments={}
    if 'windows_installer' in manifest:
        if not isinstance(windows,dict) or windows.get('manifest')!='installer_manifest.json':
            raise ValueError('Release manifest has an invalid Windows installer record.')
        arguments=dict(installer=_bundle_file(output,windows.get('package')),
                       installer_manifest=output/'installer_manifest.json')
    if installer is not None:
        if (not arguments or installer.resolve()!=arguments['installer'].resolve()
                or installer_manifest.resolve()!=arguments['installer_manifest'].resolve()):
            raise ValueError('Explicit installer files must match the finalized release bundle.')
    # Rebuild into a disposable directory to check source, metadata, XML and hashes.
    # The EXE is verified/extracted, never recompiled: NSIS outputs are not
    # assumed reproducible across different operating systems or toolchains.
    with tempfile.TemporaryDirectory(prefix='hidrocaumax-release-check-') as folder:
        if arguments:
            checked=build(DEFAULT_ROOT/'src'/PLUGIN_ID,Path(folder),manifest['tag'],repository,
                          output/manifest['package'],**arguments)
        else:
            # The workflow may use this newer publisher with an older tag's
            # ZIP-only builder, which does not know the optional arguments.
            checked=build(DEFAULT_ROOT/'src'/PLUGIN_ID,Path(folder),manifest['tag'],repository,
                          output/manifest['package'])
        if checked != manifest:
            raise ValueError('Release manifest no longer matches plugin source and ZIP.')
        for name in ('plugins.xml','SHA256SUMS.txt','release_notes.md'):
            if (Path(folder)/name).read_bytes() != (output/name).read_bytes():
                raise ValueError('Release bundle is inconsistent: '+name)
        if arguments:
            for name in ('index.html','plugins.xml','SHA256SUMS.txt','.nojekyll'):
                if (Path(folder)/'site'/name).read_bytes()!=(output/'site'/name).read_bytes():
                    raise ValueError('Pages bundle is inconsistent: '+name)
    assets=[output/manifest['package'],output/'SHA256SUMS.txt',output/'plugins.xml',output/'release_manifest.json']
    if arguments:
        assets.extend((arguments['installer'],arguments['installer_manifest']))
    return manifest,assets


def verify_assets(assets: list[Path], tag: str, repository: str, digests: dict[str,str]) -> None:
    with tempfile.TemporaryDirectory(prefix='hidrocaumax-release-assets-') as folder:
        for path in assets:
            run(['gh','release','download',tag,'--repo',repository,'--pattern',path.name,'--dir',folder])
            if sha256(Path(folder)/path.name)!=digests[path.name]:
                raise ValueError('Existing release asset differs: '+path.name)


def _unchanged_assets(assets: list[Path], digests: dict[str,str]) -> None:
    for path in assets:
        if sha256(path)!=digests[path.name]:
            raise ValueError('Local release asset changed after validation: '+path.name)


def _committed_installer_sources(output: Path) -> None:
    document=json.loads((output/'installer_manifest.json').read_text(encoding='utf-8'))
    sources=document.get('build_sources')
    if not isinstance(sources,dict) or not sources:
        raise ValueError('Installer manifest must identify its versioned build sources.')
    tracked=set(run(['git','ls-files','scripts','packaging','src/'+PLUGIN_ID]).stdout.splitlines())
    for name,digest in sources.items():
        relative=Path(name)
        if (not isinstance(name,str) or relative.is_absolute() or '..' in relative.parts
                or '\\' in name or ':' in name or name not in tracked):
            raise ValueError('Every installer build source must be committed before publication.')
        path=DEFAULT_ROOT/relative
        if path.is_symlink() or sha256(path)!=digest:
            raise ValueError('Installer build source differs from its manifest: '+name)


def publish(output: Path, repository: str, dry_run: bool=False, verify_only: bool=False,
            installer: Path | None=None, installer_manifest: Path | None=None) -> None:
    if dry_run and verify_only:
        raise ValueError('Choose local --dry-run or remote --verify-only, not both.')
    manifest,assets=validate_bundle(output,repository,installer,installer_manifest)
    tag=manifest['tag']
    if dry_run:
        print(json.dumps(dict(action='validate-only',repository=repository,tag=tag,
            assets=[path.name for path in assets],sha256=manifest['sha256']),indent=2))
        return
    digests={path.name:sha256(path) for path in assets}
    # The release must be built from the exact committed/tagged source, never a
    # branch accidentally selected by a manual workflow dispatch.
    head=run(['git','rev-parse','HEAD']).stdout.strip()
    paths=['src','scripts','.github']
    if 'windows_installer' in manifest:
        paths.append('packaging')
    run(['git','diff','--exit-code','HEAD','--',*paths])
    tracked=set(run(['git','ls-files','src/'+PLUGIN_ID]).stdout.splitlines())
    if any('src/'+name not in tracked for name in manifest['source_files']):
        raise ValueError('Every packaged source file must be committed before publication.')
    if 'windows_installer' in manifest:
        _committed_installer_sources(output)
    reference=api(f'repos/{repository}/git/ref/tags/{tag}')['object']
    while reference['type']=='tag':
        reference=api(f'repos/{repository}/git/tags/{reference["sha"]}')['object']
    if reference['type']!='commit' or reference['sha']!=head:
        raise ValueError('Remote release tag does not point to this checkout commit.')
    existing=api(f'repos/{repository}/releases/tags/{tag}',missing_ok=True)
    if existing is None:
        if verify_only:
            raise ValueError('Release does not exist; verification never creates it.')
        _unchanged_assets(assets,digests)
        command=['gh','release','create',tag,*map(str,assets),'--repo',repository,
            '--verify-tag','--title',('HidroCAUMAX v1.0' if tag == 'v1.0' else 'HidroCAUMAX '+manifest['version']),
            '--notes-file',str(output/'release_notes.md')]
        if manifest['experimental']:
            command.append('--prerelease')
        run(command)
        existing=api(f'repos/{repository}/releases/tags/{tag}')
    if existing.get('draft') or bool(existing.get('prerelease'))!=manifest['experimental']:
        raise ValueError('Existing release channel/draft differs from metadata; no replacement performed.')
    names={item['name'] for item in existing['assets']}
    missing=[path for path in assets if path.name not in names]
    if verify_only and missing:
        raise ValueError('Release lacks required assets; verification never uploads them.')
    if missing and names-set(digests):
        raise ValueError('Incomplete release has assets outside the validated bundle; no upload performed.')
    # Validate every already-published bundle asset before the first upload.
    # A mismatching file must never be repaired by replacing or deleting it.
    verify_assets([path for path in assets if path.name in names],tag,repository,digests)
    if missing:
        _unchanged_assets(missing,digests)
        release_id=existing['id']
        # Without --clobber, a concurrently uploaded name causes a safe failure.
        # A partial upload can be retried: matching files are verified and kept.
        run(['gh','release','upload',tag,*map(str,missing),'--repo',repository])
        existing=api(f'repos/{repository}/releases/tags/{tag}')
        if existing['id']!=release_id:
            raise ValueError('Release changed during asset upload; verification stopped.')
        if existing.get('draft') or bool(existing.get('prerelease'))!=manifest['experimental']:
            raise ValueError('Existing release channel/draft differs from metadata; no replacement performed.')
        if not set(digests)<={item['name'] for item in existing['assets']}:
            raise ValueError('Release still lacks required assets after upload.')
        verify_assets(assets,tag,repository,digests)
    result=dict(ok=True,release=existing['html_url'],sha256=manifest['sha256'])
    if 'windows_installer' in manifest:
        result['windows_installer_sha256']=manifest['windows_installer']['sha256']
    if verify_only:
        result['action']='verify-only'
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=DEFAULT_ROOT/'dist')
    parser.add_argument('--repository',default=DEFAULT_REPOSITORY)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run',action='store_true')
    mode.add_argument('--verify-only',action='store_true',help='Verify existing remote assets without creating or uploading anything.')
    parser.add_argument('--installer',type=Path,help='Optional explicit EXE path; must match the finalized bundle.')
    parser.add_argument('--installer-manifest',type=Path,help='Optional explicit installer manifest; requires --installer.')
    args=parser.parse_args()
    publish(args.output.resolve(),args.repository,args.dry_run,args.verify_only,
            args.installer,args.installer_manifest)
