"""Check actual venv/pip isolation without downloading or loading GPU binaries."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import venv

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('share_cuda_libraries', ROOT / 'infra/docker/build/compute/share-cuda-libraries.py')
sharing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sharing)


class CudaLibrariesTest(unittest.TestCase):
    def fixture(self, root, version='12.4.127'):
        source, target = root / 'system', root / 'worker'
        source.mkdir(); target.mkdir()
        (source / 'nvidia/cuda_runtime/lib').mkdir(parents=True)
        (source / 'nvidia/cuda_runtime/lib/libcudart.so.12').write_bytes(b'one shared library')
        metadata = source / f'nvidia_cuda_runtime_cu12-{version}.dist-info'
        metadata.mkdir()
        (metadata / 'METADATA').write_text(f'Metadata-Version: 2.1\nName: nvidia-cuda-runtime-cu12\nVersion: {version}\n')
        (metadata / 'RECORD').write_text(f'{metadata.name}/METADATA,,\n')
        lock = root / 'cuda.lock'
        lock.write_text('nvidia-cuda-runtime-cu12==12.4.127\n')
        return source, target, lock

    def test_shared_wheel_satisfies_real_pip_without_exposing_system_packages(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, _, lock = self.fixture(root)
            worker, cpu = root / 'onnx', root / 'cpu'
            venv.EnvBuilder(with_pip=True).create(worker)
            venv.EnvBuilder().create(cpu)
            python = worker / 'bin/python'
            site = Path(subprocess.check_output([str(python), '-I', '-c',
                        'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip())
            (source / 'torch').mkdir()
            (source / 'torch/__init__.py').write_text('raise RuntimeError("must not import system Torch")')
            sharing.share(source, site, lock)
            subprocess.run([str(python), '-I', '-m', 'pip', 'install', '--no-index', '--no-deps',
                            '--disable-pip-version-check', '-r', str(lock)], check=True, capture_output=True)
            subprocess.run([str(python), '-I', '-m', 'pip', 'check'], check=True, capture_output=True)
            library = site / 'nvidia/cuda_runtime/lib/libcudart.so.12'
            self.assertTrue(library.samefile(source / 'nvidia/cuda_runtime/lib/libcudart.so.12'))
            script = ('import json, importlib.util; from importlib.metadata import distributions; '
                      'print(json.dumps([sorted(d.metadata["Name"] for d in distributions()), '
                      'importlib.util.find_spec("torch") is None]))')
            gpu_packages, no_torch = json.loads(subprocess.check_output([str(python), '-I', '-c', script]))
            cpu_packages, _ = json.loads(subprocess.check_output([str(cpu / 'bin/python'), '-I', '-c', script]))
            self.assertIn('nvidia-cuda-runtime-cu12', gpu_packages)
            self.assertTrue(no_torch)
            self.assertNotIn('nvidia-cuda-runtime-cu12', cpu_packages)

    def test_mismatched_wheels_stop_before_linking(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, target, lock = self.fixture(Path(temporary), version='12.1.0')
            with self.assertRaisesRegex(ValueError, 'exactly 12.4.127'):
                sharing.share(source, target, lock)
            self.assertEqual(list(target.iterdir()), [])

    def test_existing_worker_libraries_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, target, lock = self.fixture(Path(temporary))
            existing = target / 'nvidia'; existing.mkdir()
            with self.assertRaisesRegex(ValueError, 'already contains'):
                sharing.share(source, target, lock)
            self.assertFalse(existing.is_symlink())
            self.assertEqual(list(target.iterdir()), [existing])
