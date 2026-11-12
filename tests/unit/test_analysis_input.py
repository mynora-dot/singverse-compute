"""Parsing shares bounded memory and only reclaims an inactive task's workspace."""
import json
from contextlib import nullcontext
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, PropertyMock, patch
import uuid

from singverse_compute.application.audio_roles import AudioTask
from singverse_compute.application.fast_audio_roles import FastAudioRoles
from singverse_compute.bootstrap import create_app
from singverse_compute.core.config import Settings
from singverse_compute.core.errors import ComputeError
from singverse_compute.domain.transcoding import Media, Stream
from singverse_compute.infrastructure.media.audio_workspace import acquire
from singverse_compute.infrastructure.media.remote_tags import BLOCK, RangeFile, ReadStopped


class AnalysisInputTest(unittest.TestCase):
    def test_failed_and_cancelled_output_cleanup_never_holds_worker_slot(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled), tempfile.TemporaryDirectory() as temporary:
                worker = create_app(Settings(transcode_root=Path(temporary))).state.audio_roles
                identifier = str(uuid.uuid4())
                partial = worker.root / f'{identifier}.part'
                media = Media('mkv', 120, 1000, (Stream('stream:0', 0, 'audio', 'aac'), Stream('stream:1', 1, 'audio', 'aac')))
                real_unlink = Path.unlink
                def fail_analysis(source, network, audio, duration, task):
                    partial.write_bytes(b'unfinished output')
                    if cancelled:
                        task.cancel.set()
                    raise ComputeError(409 if cancelled else 422, 'injected execution error')
                def failed_unlink(path, *args, **kwargs):
                    if path == partial:
                        raise PermissionError('busy unfinished output')
                    return real_unlink(path, *args, **kwargs)
                try:
                    with patch.object(type(worker.transcoding), 'available', new_callable=PropertyMock, return_value=True), patch.object(worker.transcoding, 'input', side_effect=lambda *args, **kwargs: nullcontext(('fixture', False))), patch.object(worker.transcoding.probe, 'read_audio', return_value=media), patch.object(FastAudioRoles, 'available', new_callable=PropertyMock, return_value=True), patch.object(worker, '_fast_analyze', side_effect=fail_analysis) as infer, patch.object(Path, 'unlink', failed_unlink):
                        worker.submit(identifier, identifier, 'FAST', callback_origin='http://127.0.0.1:18080')
                        worker.thread.join(5)
                        self.assertFalse(worker.thread.is_alive())
                        self.assertFalse(worker.active)
                        self.assertTrue(worker.slot.acquire(blocking=False))
                        worker.slot.release()
                        result = worker.view(identifier)
                        self.assertEqual(result['state'], 'CANCELLED' if cancelled else 'FAILED')
                        self.assertEqual(result['result']['cleanupState'], 'PENDING')
                        self.assertTrue(result['result']['cleanupError'])
                        self.assertNotIn('cleanupPaths', result['result'])
                        receipt = json.loads((worker.root / f'{identifier}.json').read_text())
                        self.assertEqual(receipt['result']['cleanupPaths'], [partial.name])
                        self.assertEqual(receipt['result']['cleanupState'], 'PENDING')
                        infer.assert_called_once()
                    with patch.object(worker, '_fast_analyze') as no_repeat:
                        result = worker.view(identifier)
                        self.assertEqual(result['state'], 'CANCELLED' if cancelled else 'FAILED')
                        self.assertEqual(result['result']['cleanupState'], 'RELEASED')
                        self.assertFalse(partial.exists())
                        no_repeat.assert_not_called()
                finally:
                    worker.close()

    def test_startup_registers_failed_orphan_cleanup_before_first_path_receipt(self):
        import shutil
        for receipt_exists in (False, True):
            with self.subTest(receipt=receipt_exists), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / 'audio-roles'
                root.mkdir()
                identifier = str(uuid.uuid4())
                work = root / f'work-{identifier}-interrupted'
                work.mkdir()
                (work / '.singverse-audio-workspace').write_text(identifier)
                (work / 'input.wav').write_bytes(b'leftover')
                if receipt_exists:
                    (root / f'{identifier}.json').write_text(json.dumps({'id': identifier, 'state': 'SUCCEEDED', 'result': {'state': 'CONFIDENT'}}))
                original = shutil.rmtree
                def failed(path, *args, **kwargs):
                    if Path(path) == work:
                        raise PermissionError('busy orphan')
                    return original(path, *args, **kwargs)
                with patch('singverse_compute.application.audio_roles.shutil.rmtree', side_effect=failed):
                    worker = create_app(Settings(transcode_root=Path(temporary))).state.audio_roles
                    result = worker.view(identifier)
                    self.assertEqual(result['state'], 'SUCCEEDED' if receipt_exists else 'FAILED')
                    self.assertEqual(result['result']['cleanupState'], 'PENDING')
                    receipt = json.loads((root / f'{identifier}.json').read_text())
                    self.assertEqual(receipt['result']['cleanupPaths'], [work.name])
                try:
                    result = worker.view(identifier)
                    self.assertEqual(result['result']['cleanupState'], 'RELEASED')
                    self.assertFalse(work.exists())
                finally:
                    worker.close()

    def test_cancel_reports_pending_until_live_worker_releases_its_lease(self):
        with tempfile.TemporaryDirectory() as temporary:
            worker = create_app(Settings(transcode_root=Path(temporary))).state.audio_roles
            identifier = str(uuid.uuid4())
            task = AudioTask(identifier, 'RUNNING')
            worker.tasks[identifier] = task
            work = worker.root / f'work-{identifier}-active'
            work.mkdir()
            (work / 'sample.wav').write_bytes(b'active')
            partial = worker.root / f'{identifier}.part'
            partial.write_bytes(b'active encoding')
            task.result['cleanupPaths'] = [work.name]
            owner = acquire(worker.root, identifier)
            try:
                self.assertEqual(worker.cancel(identifier)['result']['cleanupState'], 'PENDING')
                self.assertEqual(worker.view(identifier)['result']['cleanupState'], 'PENDING')
                self.assertTrue(work.exists())
                with self.assertRaises(ComputeError) as raised:
                    worker.release(identifier)
                self.assertEqual(raised.exception.status, 409)
                self.assertTrue(partial.exists())
            finally:
                owner.close()
            try:
                result = worker.view(identifier)
                self.assertEqual(result['state'], 'CANCELLED')
                self.assertEqual(result['result']['cleanupState'], 'RELEASED')
                self.assertFalse(work.exists())
                worker.release(identifier)
                self.assertFalse(partial.exists())
            finally:
                worker.close()

    def test_cleanup_failures_preserve_success_and_retry_without_inference(self):
        import shutil
        for retry in ('poll', 'restart'):
            with self.subTest(retry=retry), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                worker = create_app(Settings(transcode_root=root, minimum_free_bytes=0)).state.audio_roles
                identifier = str(uuid.uuid4())
                media = Media('mkv', 120, 1000, (Stream('stream:0', 0, 'audio', 'aac'), Stream('stream:1', 1, 'audio', 'aac')))
                real_unlink, real_rmtree = Path.unlink, shutil.rmtree
                def unlink(path, *args, **kwargs):
                    if path.suffix == '.wav':
                        raise PermissionError('busy sample')
                    return real_unlink(path, *args, **kwargs)
                def rmtree(path, *args, **kwargs):
                    if Path(path).name.startswith('work-' + identifier + '-'):
                        raise PermissionError('busy workspace')
                    return real_rmtree(path, *args, **kwargs)
                def extract(command, task):
                    for value in command:
                        if value.endswith('.wav'):
                            Path(value).write_bytes(b'sample')
                try:
                    with patch.object(type(worker.transcoding), 'available', new_callable=PropertyMock, return_value=True), patch.object(worker.transcoding, 'input', side_effect=lambda *args, **kwargs: nullcontext(('fixture', False))), patch.object(worker.transcoding.probe, 'read_audio', return_value=media), patch.object(FastAudioRoles, 'available', new_callable=PropertyMock, return_value=True), patch.object(worker, '_run', side_effect=extract), patch.object(worker.fast, 'predict', return_value=[.8, .05]*3) as infer, patch.object(Path, 'unlink', unlink), patch('singverse_compute.application.audio_roles.shutil.rmtree', side_effect=rmtree):
                        worker.submit(identifier, identifier, 'FAST', callback_origin='http://127.0.0.1:18080')
                        worker.thread.join(5)
                        self.assertFalse(worker.thread.is_alive())
                        result = worker.view(identifier)
                        self.assertEqual(result['state'], 'SUCCEEDED', result)
                        self.assertEqual(result['result']['state'], 'CONFIDENT')
                        self.assertEqual(result['result']['cleanupState'], 'PENDING')
                        self.assertTrue(result['result']['cleanupError'])
                        self.assertNotIn('cleanupPaths', result['result'])
                        receipt = json.loads((worker.root / f'{identifier}.json').read_text())
                        self.assertEqual(receipt['state'], 'SUCCEEDED')
                        self.assertEqual(receipt['result']['cleanupState'], 'PENDING')
                        self.assertEqual(len(receipt['result']['cleanupPaths']), 1)
                        infer.assert_called_once()
                    if retry == 'restart':
                        worker.close()
                        worker = create_app(Settings(transcode_root=root)).state.audio_roles
                    with patch.object(worker.fast, 'predict') as no_repeat:
                        result = worker.view(identifier)
                        self.assertEqual(result['state'], 'SUCCEEDED')
                        self.assertEqual(result['result']['cleanupState'], 'RELEASED')
                        self.assertEqual(result['result']['cleanupError'], '')
                        self.assertNotIn('cleanupPaths', result['result'])
                        self.assertFalse(list(worker.root.glob('work-*')))
                        receipt = json.loads((worker.root / f'{identifier}.json').read_text())
                        self.assertEqual(receipt['result']['cleanupState'], 'RELEASED')
                        no_repeat.assert_not_called()
                finally:
                    worker.close()

    def test_audio_consumers_share_one_memory_block_and_close_discards_it(self):
        response = Mock(status=206, headers={'Content-Range': f'bytes 0-{BLOCK-1}/{2*BLOCK}'})
        response.read.return_value = b'a' * BLOCK
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = response
        with patch('singverse_compute.infrastructure.media.remote_tags.build_opener', return_value=opener):
            source = RangeFile('http://127.0.0.1:18080', str(uuid.uuid4()), 't'*64, 'audio', 2*BLOCK, resource='audio-input')
            with source:
                # A probe and a later extraction reopen/seeks the same task input.
                self.assertEqual(source.read(16), b'a'*16)
                source.seek(0)
                self.assertEqual(source.read(32), b'a'*32)
                self.assertEqual(source.requests, 1)
                self.assertEqual(source.bytes_read, BLOCK)
                self.assertIn('/audio-input/', opener.open.call_args.args[0].full_url)
                source.reason = 'UNSTABLE'
                source.seek(0)
                with self.assertRaises(ReadStopped):
                    source.read(1)
            self.assertEqual(source.cache, {})
            self.assertTrue(source.closed)

    def test_startup_preserves_live_work_and_reclaims_it_after_lease_ends(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary)
            root = cache / 'audio-roles'
            root.mkdir()
            identifier = str(uuid.uuid4())
            record = root / f'{identifier}.json'
            record.write_text(json.dumps({'id': identifier, 'state': 'RUNNING'}))
            partial = root / f'{identifier}.part'
            partial.write_bytes(b'active')
            work = root / f'work-{identifier}-active'
            work.mkdir()
            wav = work / 'sample.wav'
            wav.write_bytes(b'active')
            unknown = root / f'work-{uuid.uuid4()}-unknown'
            unknown.mkdir()
            (unknown / 'unrelated').write_bytes(b'keep')
            owner = acquire(root, identifier)
            self.assertIsNotNone(owner)
            try:
                app = create_app(Settings(transcode_root=cache))
                try:
                    self.assertTrue(wav.exists())
                    self.assertTrue(partial.exists())
                    self.assertEqual(json.loads(record.read_text())['state'], 'RUNNING')
                finally:
                    app.state.audio_roles.close()
            finally:
                owner.close()
            app = create_app(Settings(transcode_root=cache))
            try:
                self.assertFalse(work.exists())
                self.assertFalse(partial.exists())
                self.assertTrue((unknown / 'unrelated').exists())
                self.assertEqual(app.state.audio_roles.view(identifier)['error'], 'NODE_RESTARTED')
            finally:
                app.state.audio_roles.close()

    def test_fast_discards_previous_round_wavs_and_failure_cleans_current_round(self):
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as temporary:
                worker = create_app(Settings(transcode_root=Path(temporary))).state.audio_roles
                task = AudioTask(str(uuid.uuid4()))
                audio = [SimpleNamespace(index=0, title=''), SimpleNamespace(index=1, title='')]
                previous, rounds = [], []
                def run(command, task):
                    for item in command:
                        if item.endswith('.wav'):
                            Path(item).write_bytes(b'current sample')
                def predict(paths, cancel):
                    self.assertFalse(any(path.exists() for path in previous))
                    self.assertTrue(all(path.exists() for path in paths))
                    previous[:] = paths
                    rounds.append(paths)
                    if len(rounds) == 2 and fail:
                        raise RuntimeError('inference failed')
                    return [.02, .02, .20, .02, .12, .16] if len(rounds) == 1 else [.14, .02, .13, .02, .10, .02]
                try:
                    with patch.object(FastAudioRoles, 'available', new_callable=PropertyMock, return_value=True), patch.object(worker, '_run', side_effect=run), patch.object(worker.fast, 'predict', side_effect=predict):
                        if fail:
                            with self.assertRaises(RuntimeError):
                                worker._fast_analyze('', False, audio, 120, task)
                        else:
                            self.assertEqual(worker._fast_analyze('', False, audio, 120, task)['state'], 'CONFIDENT')
                    self.assertEqual(len(rounds), 2)
                    self.assertFalse(any(path.exists() for paths in rounds for path in paths))
                    self.assertFalse(list(worker.root.glob('work-*')))
                finally:
                    worker.close()
