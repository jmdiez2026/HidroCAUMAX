"""Publish/verify the prepared bundle with an already authenticated GitHub CLI.

Never replaces an existing asset. A retry validates its bytes, then leaves
Pages deployment to the workflow. Use --dry-run to inspect the local bundle.
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


def validate_bundle(output: Path, repository: str) -> tuple[dict,list[Path]]:
    manifest=json.loads((output/'release_manifest.json').read_text(encoding='utf-8'))
    if manifest['repository'] != validate_repository(repository):
        raise ValueError('Release manifest targets a different repository.')
    # Rebuild into a disposable directory to check source, metadata, XML and hashes.
    with tempfile.TemporaryDirectory(prefix='hidrocaumax-release-check-') as folder:
        checked=build(DEFAULT_ROOT/'src'/PLUGIN_ID,Path(folder),manifest['tag'],repository,
                      output/manifest['package'])
        if checked != manifest:
            raise ValueError('Release manifest no longer matches plugin source and ZIP.')
        for name in ('plugins.xml','SHA256SUMS.txt','release_notes.md'):
            if (Path(folder)/name).read_bytes() != (output/name).read_bytes():
                raise ValueError('Release bundle is inconsistent: '+name)
    assets=[output/manifest['package'],output/'SHA256SUMS.txt',output/'plugins.xml',output/'release_manifest.json']
    return manifest,assets


def publish(output: Path, repository: str, dry_run: bool=False) -> None:
    manifest,assets=validate_bundle(output,repository)
    tag=manifest['tag']
    if dry_run:
        print(json.dumps(dict(action='validate-only',repository=repository,tag=tag,
            assets=[path.name for path in assets],sha256=manifest['sha256']),indent=2))
        return
    # The release must be built from the exact committed/tagged source, never a
    # branch accidentally selected by a manual workflow dispatch.
    head=run(['git','rev-parse','HEAD']).stdout.strip()
    run(['git','diff','--exit-code','HEAD','--','src','scripts','.github'])
    tracked=set(run(['git','ls-files','src/'+PLUGIN_ID]).stdout.splitlines())
    if any('src/'+name not in tracked for name in manifest['source_files']):
        raise ValueError('Every packaged source file must be committed before publication.')
    reference=api(f'repos/{repository}/git/ref/tags/{tag}')['object']
    while reference['type']=='tag':
        reference=api(f'repos/{repository}/git/tags/{reference["sha"]}')['object']
    if reference['type']!='commit' or reference['sha']!=head:
        raise ValueError('Remote release tag does not point to this checkout commit.')
    existing=api(f'repos/{repository}/releases/tags/{tag}',missing_ok=True)
    if existing is None:
        command=['gh','release','create',tag,*map(str,assets),'--repo',repository,
            '--verify-tag','--title','HidroCAUMAX '+manifest['version'],
            '--notes-file',str(output/'release_notes.md')]
        if manifest['experimental']:
            command.append('--prerelease')
        run(command)
        existing=api(f'repos/{repository}/releases/tags/{tag}')
    if existing.get('draft') or bool(existing.get('prerelease'))!=manifest['experimental']:
        raise ValueError('Existing release channel/draft differs from metadata; no replacement performed.')
    names={item['name'] for item in existing['assets']}
    if not {path.name for path in assets} <= names:
        raise ValueError('Existing release lacks required assets; no overwrite performed.')
    with tempfile.TemporaryDirectory(prefix='hidrocaumax-release-assets-') as folder:
        for path in assets:
            run(['gh','release','download',tag,'--repo',repository,'--pattern',path.name,'--dir',folder])
            if sha256(Path(folder)/path.name)!=sha256(path):
                raise ValueError('Existing release asset differs: '+path.name)
    print(json.dumps(dict(ok=True,release=existing['html_url'],sha256=manifest['sha256']),indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=DEFAULT_ROOT/'dist')
    parser.add_argument('--repository',default=DEFAULT_REPOSITORY)
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    publish(args.output.resolve(),args.repository,args.dry_run)
