import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from singverse_compute.application.audio_catalog import RKNN
from singverse_compute.application.audio_models import AudioModels
from singverse_compute.application.audio_roles import AudioRoles
from singverse_compute.core.errors import ComputeError


def model_files(chip='rk3566', graph=b'controlled RKNN graph', **changes):
    metadata = {'profile': 'uvr-mdxnet-9482-v1', 'target': chip, 'toolkit': '2.3.2',
                'sourceSha256': '9d78f8566fa8198065214ab628be1de966a500c57786695aa4b13e2b27a7727d',
                'size': len(graph), 'sha256': hashlib.sha256(graph).hexdigest(), **changes}
    return {'manifest.json': json.dumps(metadata).encode(), 'uvr.rknn': graph}


def archive(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, data in files.items():
            bundle.writestr(name, data)
    output.seek(0)
    return output


class RknnModelInstallTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        audio = SimpleNamespace(root=root/'tasks', repo=root/'htdemucs', runtime_available=False,
            digest=AudioRoles.digest, settings=SimpleNamespace(minimum_free_bytes=0, audio_memory_bytes=1024))
        audio.root.mkdir()
        self.manager = AudioModels(audio)
        patcher = patch('singverse_compute.infrastructure.hardware.device_tree', return_value='rockchip,rk3566\0')
        patcher.start()
        self.addCleanup(patcher.stop)

    def install(self, files):
        self.manager.begin('IMPORTING', RKNN)
        try:
            with self.manager.stage() as stage:
                self.manager.import_bundle(archive(files), Path(stage), RKNN)
        except BaseException as error:
            self.manager.failed(error)
            raise

    def test_single_target_install_is_atomic_idempotent_persistent_and_unqualified(self):
        self.install(model_files())
        folder = self.manager.model_path(RKNN)
        inode = (folder/'uvr.rknn').stat().st_ino
        self.install(model_files())
        self.assertEqual((folder/'uvr.rknn').stat().st_ino, inode)
        restarted = AudioModels(self.manager.audio)
        self.assertTrue(restarted.model_valid(RKNN))
        self.assertFalse(restarted.weights_valid())
        self.assertEqual(restarted.qualified, {})
        self.assertEqual(restarted.operation['state'], 'SUCCEEDED')
        self.assertEqual(restarted.model_status(RKNN)['target'], 'rk3566')
        self.assertTrue(restarted.model_status(RKNN)['downloadSourceRequired'])

    def test_multi_target_bundle_chooses_current_chip_only(self):
        files = {chip+'/'+name: data for chip in ('rk3566', 'rk3568', 'rk3588')
                 for name, data in model_files(chip, graph=chip.encode()).items()}
        for chip in ('rk3566', 'rk3568', 'rk3588'):
            with self.subTest(chip=chip), patch('singverse_compute.infrastructure.hardware.device_tree', return_value='rockchip,'+chip):
                self.manager.repo = Path(self.temporary.name)/chip/'htdemucs'
                self.manager.repo.parent.mkdir()
                self.install(files)
                folder = self.manager.model_path(RKNN)
                self.assertEqual((folder/'uvr.rknn').read_bytes(), chip.encode())
                self.assertEqual({p.name for p in folder.iterdir()}, {'manifest.json', 'uvr.rknn'})

    def test_rejects_wrong_chip_toolkit_source_digest_and_non_object_manifest(self):
        cases = [model_files('rk3588'), model_files(toolkit='other'), model_files(sourceSha256='0'*64),
                 model_files(sha256='0'*64), model_files(size=True),
                 {'manifest.json': b'[]', 'uvr.rknn': b'graph'}]
        for files in cases:
            with self.subTest(files=files), self.assertRaises(ComputeError):
                self.install(files)
            self.assertFalse(self.manager.model_path(RKNN).exists())

    def test_invalid_or_different_bundle_preserves_existing_model(self):
        self.install(model_files())
        original = self.manager.inference_digest(RKNN)
        for files in (model_files(sha256='0'*64), model_files(graph=b'another valid graph')):
            with self.assertRaises(ComputeError):
                self.install(files)
            self.assertEqual(self.manager.inference_digest(RKNN), original)

    def test_rejects_extra_paths_missing_target_and_incomplete_variant(self):
        cases = [{**model_files(), '../escape': b'x'}, {**model_files(), 'extra': b'x'},
                 {'rk3588/'+name: data for name, data in model_files('rk3588').items()},
                 {**{'rk3566/'+name: data for name, data in model_files().items()}, 'rk3588/uvr.rknn': b'x'}]
        for files in cases:
            with self.subTest(names=list(files)), self.assertRaises(ComputeError):
                self.install(files)
            self.assertFalse(self.manager.model_path(RKNN).exists())

    def test_rejects_duplicate_and_symbolic_link_members(self):
        for kind in ('duplicate', 'symlink'):
            output = io.BytesIO()
            with zipfile.ZipFile(output, 'w') as bundle:
                bundle.writestr('manifest.json', model_files()['manifest.json'])
                if kind == 'duplicate':
                    import warnings
                    with warnings.catch_warnings():
                        warnings.simplefilter('ignore', UserWarning)
                        bundle.writestr('manifest.json', b'duplicate')
                    bundle.writestr('uvr.rknn', b'graph')
                else:
                    item = zipfile.ZipInfo('uvr.rknn')
                    item.create_system = 3
                    item.external_attr = 0o120777 << 16
                    bundle.writestr(item, '/outside')
            output.seek(0)
            self.manager.begin('IMPORTING', RKNN)
            with self.manager.stage() as stage, self.assertRaises(ComputeError):
                self.manager.import_bundle(output, Path(stage), RKNN)
            self.manager.failed(ComputeError(422, 'invalid'))
            self.assertFalse(self.manager.model_path(RKNN).exists())

    def test_cancellation_and_unknown_board_never_install(self):
        self.manager.begin('IMPORTING', RKNN)
        self.manager.stop()
        with self.manager.stage() as stage, self.assertRaises(ComputeError):
            self.manager.import_bundle(archive(model_files()), Path(stage), RKNN)
        self.manager.failed(ComputeError(409, 'cancelled'))
        with patch('singverse_compute.infrastructure.hardware.device_tree', return_value=''):
            with self.assertRaisesRegex(ComputeError, '设备树'):
                self.install(model_files())
        self.assertFalse(self.manager.model_path(RKNN).exists())

    def test_download_passes_verified_archive_through_same_installer(self):
        data = archive(model_files()).getvalue()
        source = {'url': 'https://models.example.test/rk.zip', 'sha256': hashlib.sha256(data).hexdigest()}
        def downloaded(value, destination, maximum, cancel, progress):
            self.assertEqual(value, source)
            destination.write_bytes(data)
            progress(len(data))
        with patch('singverse_compute.infrastructure.model_download.download_bundle', side_effect=downloaded):
            self.manager.download(RKNN, source)
            self.manager.thread.join(5)
        self.assertEqual(self.manager.operation['state'], 'SUCCEEDED')
        self.assertTrue(self.manager.model_valid(RKNN))
        self.assertFalse(self.manager.qualified)

    def test_download_requires_explicit_source_and_never_changes_other_model_sources(self):
        for source in (None, {}, {'url': 'https://models.example.test/rk.zip'},
                       {'url': 'file:///private/model.zip', 'sha256': '0'*64}):
            with self.assertRaises(ComputeError):
                self.manager.download(RKNN, source)
        with self.assertRaises(ComputeError):
            self.manager.download('htdemucs', {'url': 'https://other.example.test', 'sha256': '0'*64})
        self.assertEqual(self.manager.operation['state'], 'IDLE')
