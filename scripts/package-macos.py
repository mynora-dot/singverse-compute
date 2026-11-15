#!/usr/bin/env python3
"""Package an immutable Apple Silicon native installer without local data or models."""
import argparse
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import tarfile

from build_version import release_version

ROOT = Path(__file__).resolve().parents[1]
FOLDER = 'singverse-compute-macos'


def package(root, output, version, revision):
    release_version({'RELEASE_TAG': 'v' + version})
    if not re.fullmatch(r'[a-f0-9]{40}', revision):
        raise ValueError('Use the exact Release commit for the Mac package')
    names = {
        'scripts/macos-compute.py', 'scripts/macos-install.py',
        'scripts/setup-separation-runtime.py', 'scripts/setup-transcode-tools.py',
        'tools/jellyfin-ffmpeg.lock.json', 'MACOS.md',
    }
    names.update(str(path.relative_to(root)) for path in (root/'singverse_compute').rglob('*.py'))
    names.update(str(path.relative_to(root)) for path in (root/'singverse_compute').rglob('*LICENSE*'))
    names.update(path.name for path in root.glob('requirements*.lock'))
    names.update(path.name for pattern in ('LICENSE*', 'NOTICE*') for path in root.glob(pattern))
    files = {}
    for name in sorted(names):
        path = root/name
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('Package input must be a regular project file: ' + name)
        files[name] = path.read_bytes()
    metadata = dict(version=version, revision=revision, variant='macos',
                    runtimeLock=hashlib.sha256(b''.join(data for name, data in files.items() if name.endswith('.lock'))).hexdigest(),
                    sourceDigest=hashlib.sha256(b''.join(name.encode()+data for name, data in files.items() if name.startswith('singverse_compute/'))).hexdigest())
    files['singverse_compute/core/build.json'] = json.dumps(metadata, sort_keys=True).encode()
    for label, action in (('Install', 'install'), ('Start', 'start'), ('Stop', 'stop')):
        files[label+'.command'] = ('#!/bin/sh\nset -eu\ncd "$(dirname "$0")"\n'
            'exec python3.12 scripts/macos-install.py ' + action + '\n').encode()
    output.mkdir(parents=True, exist_ok=True)
    target = output/f'singverse-compute-macos-arm64-{version}.tar.gz'
    # Stable names, modes, timestamps and gzip headers make retries byte-identical.
    with target.open('wb') as stream, gzip.GzipFile(filename='', mode='wb', fileobj=stream, mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w') as archive:
            for name, data in sorted(files.items()):
                member = tarfile.TarInfo(FOLDER+'/'+name)
                member.size = len(data)
                member.mode = 0o755 if name.endswith('.command') else 0o644
                archive.addfile(member, io.BytesIO(data))
    checksum = target.with_suffix(target.suffix+'.sha256')
    checksum.write_text(hashlib.sha256(target.read_bytes()).hexdigest()+'  '+target.name+'\n')
    return target, checksum


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('dist/macos'))
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    version = release_version()
    paths = package(ROOT, args.output, version, os.environ['SOURCE_REVISION'])
    if args.publish:
        repository = os.environ['GITHUB_REPOSITORY']
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
            raise ValueError('Invalid GitHub repository')
        spec = importlib.util.spec_from_file_location('release_asset', ROOT/'infra/docker/release-artifact.py')
        assets = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(assets)
        for path in paths:
            assets.save_file(repository, os.environ['RELEASE_TAG'], path.name, path)
    for path in paths:
        print(path)


if __name__ == '__main__':
    main()
