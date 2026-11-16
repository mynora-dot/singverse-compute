"""Share pinned NVIDIA wheels with the ONNX venv without exposing system packages."""
import argparse
from importlib.metadata import distributions
from pathlib import Path
import re
import subprocess
import sysconfig


def share(source, target, lock):
    expected = dict(re.findall(r'^(nvidia-[a-z0-9-]+)==([^\s#]+)\s*$', lock.read_text(), re.MULTILINE))
    if not expected:
        raise ValueError('The CUDA worker lock must pin its NVIDIA libraries')
    installed = {item.metadata['Name'].lower().replace('_', '-'): item
                 for item in distributions(path=[str(source)])}
    links = {'nvidia': source / 'nvidia'}
    for name, required in expected.items():
        package = installed.get(name)
        if package is None or package.version != required:
            raise ValueError(f'{name} must already be installed at exactly {required}')
        metadata = next((path for path in package.files or []
                         if path.name == 'METADATA' and path.parent.name.endswith('.dist-info')), None)
        if metadata is None:
            raise ValueError(f'{name} is missing wheel metadata')
        folder = Path(package.locate_file(metadata)).parent
        links[folder.name] = folder
    # Validate everything before changing the otherwise isolated venv. In
    # particular, never overwrite an independently installed NVIDIA package.
    for name, origin in links.items():
        if not origin.is_dir():
            raise ValueError(f'Missing shared CUDA directory: {origin}')
        destination = target / name
        if destination.exists() or destination.is_symlink():
            raise ValueError(f'The worker already contains {destination}')
    for name, origin in links.items():
        (target / name).symlink_to(origin.resolve(), target_is_directory=True)
    print(f'Shared {len(expected)} pinned NVIDIA wheels with the isolated ONNX worker', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('venv', type=Path)
    parser.add_argument('lock', type=Path)
    args = parser.parse_args()
    target = subprocess.check_output([str(args.venv / 'bin/python'), '-I', '-c',
                                      'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip()
    share(Path(sysconfig.get_path('purelib')), Path(target), args.lock)


if __name__ == '__main__':
    main()
