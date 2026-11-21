import importlib.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'scripts'))


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


packager = module('macos_package', ROOT/'scripts/package-macos.py')
installer = module('macos_install', ROOT/'scripts/macos-install.py')


class MacosPackageTest(unittest.TestCase):
    def test_dmg_payload_excludes_private_data_and_runs_with_release_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'source'
            root.mkdir()
            shutil.copytree(ROOT/'singverse_compute', root/'singverse_compute',
                            ignore=shutil.ignore_patterns('__pycache__', 'build.json'))
            for name in ('scripts/macos-compute.py', 'scripts/macos-install.py', 'scripts/setup-separation-runtime.py',
                         'scripts/setup-transcode-tools.py', 'tools/jellyfin-ffmpeg.lock.json', 'MACOS.md'):
                path = root/name
                path.parent.mkdir(exist_ok=True)
                shutil.copyfile(ROOT/name, path)
            for path in ROOT.glob('requirements*.lock'):
                shutil.copyfile(path, root/path.name)
            for name in ('.env', 'data/token', '.venv/secret', '.tools/model.pth', 'singverse_compute/private.key'):
                path = root/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('private-fixture')
            extracted = Path(directory)/'extracted'
            def create_image(command, check):
                self.assertTrue(check)
                self.assertEqual(command[:2], ['hdiutil', 'create'])
                self.assertEqual(command[command.index('-volname')+1], 'SingVerse Compute 1.2.3')
                self.assertEqual(command[command.index('-format')+1], 'UDZO')
                stage = Path(command[command.index('-srcfolder')+1])
                shutil.copytree(stage, extracted)
                Path(command[-1]).write_bytes(b'disk-image-fixture')
            with patch.object(packager.subprocess, 'run', side_effect=create_image):
                archive, checksum = packager.package(root, Path(directory)/'one', '1.2.3', 'a'*40)
            self.assertEqual(archive.name, 'singverse-compute-macos-arm64-1.2.3.dmg')
            self.assertEqual(checksum.read_text(), hashlib.sha256(archive.read_bytes()).hexdigest()+'  '+archive.name+'\n')
            project = extracted/packager.FOLDER
            names = {str(path.relative_to(project)) for path in project.rglob('*')}
            self.assertFalse(any(name.endswith(('.env', 'token', '.key', '.pth', '.pyc')) for name in names))
            self.assertEqual((project/'Install.command').stat().st_mode & 0o777, 0o755)
            metadata = json.loads((project/'singverse_compute/core/build.json').read_text())
            self.assertEqual((metadata['version'], metadata['revision'], metadata['variant']), ('1.2.3', 'a'*40, 'macos'))
            subprocess.run([sys.executable, '-c', "from singverse_compute.core.build_info import build_info, includes_backend; "
                            "assert build_info()['version']=='1.2.3'; assert includes_backend('mps'); "
                            "assert includes_backend('cpu'); assert not includes_backend('cuda')"],
                           cwd=project, env={key: value for key, value in os.environ.items() if not key.startswith('SINGVERSE_')}, check=True)
            for label in ('Install', 'Start', 'Stop'):
                subprocess.run(['sh', '-n', str(project/(label+'.command'))], check=True)

    def test_release_retry_reuses_first_image_and_recovers_its_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'dist'
            assets = Mock()
            assets.lookup.return_value = {'id': 42}
            assets.download.side_effect = lambda repository, asset, target: target.write_bytes(b'first-dmg')
            with patch.object(packager, 'package') as package:
                image, checksum = packager.release_package(ROOT, output, '1.2.3', 'a'*40,
                                                          assets, 'owner/repo', 'v1.2.3')
            package.assert_not_called()
            self.assertEqual(image.read_bytes(), b'first-dmg')
            self.assertEqual(checksum.read_text(), hashlib.sha256(b'first-dmg').hexdigest()+'  '+image.name+'\n')
            self.assertEqual([call.args[2] for call in assets.save_file.call_args_list],
                             [image.name, checksum.name])

    def test_non_release_versions_and_non_commit_revisions_are_rejected(self):
        for version, revision in [('1.2.3-rc.1', 'a'*40), ('1.2.3', 'main'), ('1.2.3', 'a'*40+'\n')]:
            with self.subTest(version=version, revision=revision), self.assertRaises(ValueError):
                packager.package(ROOT, Path('/unused'), version, revision)

    def test_installer_uses_only_local_environments_and_leaves_models_to_admin(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(installer.platform, 'system', return_value='Darwin'), \
             patch.object(installer.platform, 'machine', return_value='arm64'), \
             patch.object(installer.sys, 'version_info', (3, 12)), \
             patch.object(installer.venv, 'EnvBuilder') as venv, patch.object(installer.subprocess, 'run') as run:
            root = Path(directory)
            installer.install(root)
            venv.return_value.create.assert_called_once_with(root/'.venv')
            commands = [call.args[0] for call in run.call_args_list]
            self.assertEqual(len(commands), 5)
            self.assertTrue(all(command[0] == str(root/'.venv/bin/python') for command in commands))
            self.assertTrue(all(call.kwargs['cwd'] == root and call.kwargs['check'] for call in run.call_args_list))
            self.assertIn('--legacy', commands[3])
            self.assertIn(str(root/'requirements-separation-mlx.lock'), commands[4])
            self.assertFalse(any('setup-audio-model' in str(command) or '--user' in command for command in commands))

    def test_installer_rejects_other_platforms_before_any_mutation(self):
        for system, architecture in [('Linux', 'arm64'), ('Darwin', 'x86_64')]:
            with self.subTest(system=system, architecture=architecture), \
                 patch.object(installer.platform, 'system', return_value=system), \
                 patch.object(installer.platform, 'machine', return_value=architecture), \
                 patch.object(installer.subprocess, 'run') as run, self.assertRaises(ValueError):
                installer.install(Path('/unused'))
            run.assert_not_called()
