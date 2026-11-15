#!/usr/bin/env python3
"""Install pinned native Mac runtimes into the extracted package directory only."""
import argparse
from pathlib import Path
import platform
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]


def install(root):
    if platform.system() != 'Darwin' or platform.machine() != 'arm64' or sys.version_info[:2] != (3, 12):
        raise ValueError('Use native Python 3.12 on an Apple Silicon Mac (M1 or later)')
    target = root/'.venv'
    if not (target/'pyvenv.cfg').is_file():
        if target.exists() and any(target.iterdir()):
            raise ValueError('Preserve the existing non-venv .venv directory before installing')
        venv.EnvBuilder(with_pip=True).create(target)
    python = str(target/'bin/python')
    commands = [
        [python, '-m', 'pip', 'install', '-r', str(root/'requirements-audio.lock')],
        [python, '-m', 'pip', 'check'],
        [python, str(root/'scripts/setup-transcode-tools.py')],
        [python, str(root/'scripts/setup-separation-runtime.py'), '--legacy'],
        [python, str(root/'scripts/setup-separation-runtime.py'), '--directory', str(root/'.venv-audio-mlx'),
         '--lock', str(root/'requirements-separation-mlx.lock'), '--role', 'accelerator'],
    ]
    for command in commands:
        subprocess.run(command, cwd=root, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('install', 'start', 'restart', 'stop', 'status'), default='install', nargs='?')
    args = parser.parse_args()
    if args.action == 'install':
        install(ROOT)
        print('安装完成。运行 Start.command 启动节点，然后在 Go 管理端配对、安装模型并验证加速与 CPU 降级。')
    else:
        python = ROOT/'.venv/bin/python'
        if not python.is_file():
            parser.error('Run Install.command first')
        subprocess.run([str(python), str(ROOT/'scripts/macos-compute.py'), args.action], cwd=ROOT, check=True)


if __name__ == '__main__':
    main()
