#!/usr/bin/env python3
"""Package checked RKNN conversion outputs for the administrator model manager."""
import argparse
import hashlib
from pathlib import Path
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from singverse_compute.infrastructure.rknn_audio import local_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('models', nargs='+', type=Path, help='One or more conversion output directories')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    variants = {}
    for directory in args.models:
        metadata, _ = local_manifest(directory)
        target = metadata['target']
        if target in variants:
            raise ValueError('Duplicate RKNN target: ' + target)
        variants[target] = directory
    # Never overwrite an existing package; readers cannot see a partial ZIP.
    temporary = args.output.with_name(args.output.name + '.part')
    stream = temporary.open('xb')
    try:
        with stream:
            with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
                for target, directory in sorted(variants.items()):
                    for name in ('manifest.json', 'uvr.rknn'):
                        member = name if len(variants) == 1 else target + '/' + name
                        bundle.write(directory/name, member)
        if temporary.stat().st_size > 512 * 1024**2:
            raise ValueError('RKNN ZIP exceeds the model manager limit')
        # A hard link publishes the complete file and fails if the destination exists.
        args.output.hardlink_to(temporary)
    finally:
        temporary.unlink(missing_ok=True)
    with args.output.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    args.output.with_name(args.output.name + '.sha256').write_text(digest + '  ' + args.output.name + '\n')
    print(str(args.output) + '\nSHA-256: ' + digest)


if __name__ == '__main__':
    main()
