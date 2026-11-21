import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('release', ROOT / 'infra/docker/release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def env(self):
        return dict(RELEASE_TAG='v1.2.3', GITHUB_REPOSITORY_OWNER='mynora-dot',
                    DOCKERHUB_NAMESPACE='custom-hub',
                    GHCR_TOKEN='test', DOCKERHUB_USERNAME='test', DOCKERHUB_TOKEN='test')

    def test_namespaces_are_independent_configuration(self):
        env = self.env()
        version, prefixes = release.config(env)
        self.assertEqual(version, '1.2.3')
        self.assertEqual(prefixes, ['ghcr.io/mynora-dot', 'docker.io/custom-hub'])
        env['GITHUB_REPOSITORY_OWNER'] = 'MyNora-Dot'
        self.assertEqual(release.config(env)[1][0], 'ghcr.io/mynora-dot')
        env['GHCR_NAMESPACE'] = 'custom-ghcr'
        self.assertEqual(release.config(env)[1][0], 'ghcr.io/custom-ghcr')

    def test_missing_destination_or_secret_fails_before_publishing(self):
        for key in ('DOCKERHUB_NAMESPACE', 'GHCR_TOKEN', 'DOCKERHUB_USERNAME', 'DOCKERHUB_TOKEN'):
            env = self.env()
            del env[key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                release.config(env)

    def test_release_tag_and_registry_validation(self):
        for key, value in [('RELEASE_TAG', 'v9.0.0-rc.1'), ('GHCR_NAMESPACE', 'a/b'),
                           ('DOCKERHUB_NAMESPACE', 'UpperCase')]:
            env = self.env()
            env[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                release.config(env)

    def test_existing_equal_tag_is_retryable_and_different_tag_is_never_written(self):
        for existing in ('sha256:equal', 'sha256:different'):
            with patch.object(release, 'remote_digest', return_value=existing), patch.object(release, 'copy_image') as publish:
                if existing.endswith('different'):
                    with self.assertRaises(ValueError):
                        release.ensure('registry/image:1.0.0', 'sha256:equal', publish)
                else:
                    release.ensure('registry/image:1.0.0', 'sha256:equal', publish)
                publish.assert_not_called()

    def test_absent_tag_is_verified_after_copy(self):
        with patch.object(release, 'remote_digest', side_effect=[None, 'sha256:expected']), patch.object(release, 'copy_image') as copy:
            release.ensure('registry/image:1.0.0', 'sha256:expected', copy)
            copy.assert_called_once()

    def test_auth_and_network_errors_do_not_count_as_absent(self):
        for error in ('unauthorized', 'connection refused', 'timeout'):
            with patch.object(release.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', error)):
                with self.assertRaises(RuntimeError):
                    release.remote_digest('registry/image:1.0.0')

    def test_latest_is_not_touched_if_any_version_destination_fails(self):
        raw = json.dumps({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json'}).encode()
        calls = []
        def ensure(ref, digest, publish):
            calls.append(ref)
            if ref.startswith('docker.io/'):
                raise RuntimeError('destination unavailable')
        with patch.dict(release.os.environ, self.env(), clear=True), patch.object(release, 'VARIANTS', {'cuda': ['amd64']}), \
             patch.object(release.subprocess, 'check_output', return_value=raw), patch.object(release, 'layout'), \
             patch.object(release, 'ensure', side_effect=ensure):
            with self.assertRaises(RuntimeError):
                release.publish(Path('/unused'))
        self.assertFalse(any(':latest' in ref for ref in calls))

    def test_latest_only_follows_every_successful_fixed_tag(self):
        raw = json.dumps({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json'}).encode()
        events = []
        def ensure(ref, digest, publish):
            events.append(('fixed', ref, digest))
            publish()
        def copy(*args):
            if args[-1].endswith(':latest-cuda'):
                events.append(('latest', args[-1]))
        with patch.dict(release.os.environ, self.env(), clear=True), patch.object(release, 'VARIANTS', {'cuda': ['amd64']}), \
             patch.object(release.subprocess, 'check_output', return_value=raw), patch.object(release, 'ensure', side_effect=ensure), \
             patch.object(release, 'layout'), patch.object(release, 'copy_image', side_effect=copy):
            release.publish(Path('/unused'))
        self.assertEqual([event[0] for event in events], ['fixed'] * 2 + ['latest'] * 2)
        self.assertEqual(len({event[2] for event in events[:2]}), 1)
        self.assertTrue(all(event[1].startswith('docker://') for event in events[-2:]))
        self.assertEqual(len({event[1] for event in events[-2:]}), 2)

    def test_version_index_failure_does_not_update_latest(self):
        raw = json.dumps({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json'}).encode()
        def ensure(ref, digest, publish):
            raise RuntimeError('index publication failed')
        with patch.dict(release.os.environ, self.env(), clear=True), patch.object(release, 'VARIANTS', {'cuda': ['amd64']}), \
             patch.object(release.subprocess, 'check_output', return_value=raw), patch.object(release, 'ensure', side_effect=ensure), \
             patch.object(release, 'layout'), patch.object(release, 'copy_image') as copy:
            with self.assertRaises(RuntimeError):
                release.publish(Path('/unused'))
            copy.assert_not_called()

    def test_registry_archives_are_bounded_to_one_runtime_and_cleaned_before_latest(self):
        raw = json.dumps({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json'}).encode()
        roots = []
        restored = []
        events = []

        def restore(args, check):
            archive = Path(args[-1])
            root = archive.parents[1]
            if root not in roots:
                self.assertTrue(all(not previous.exists() for previous in roots))
                roots.append(root)
            self.assertTrue(check)
            self.assertEqual(args[2:5], ['restore', '--name', archive.parent.name + '.oci.tar'])
            archive.parent.mkdir(parents=True)
            archive.write_bytes(b'checked archive')
            restored.append(archive.parent.name)

        def layout(directory, variant, raw, destination):
            self.assertEqual(directory, roots[-1])
            self.assertEqual({path.name for path in directory.iterdir()},
                             {f'{variant}-{arch}' for arch in release.VARIANTS[variant]})

        def copy(*args):
            self.assertTrue(all(not root.exists() for root in roots))
            events.append(('latest', args[-1]))

        with patch.dict(release.os.environ, self.env(), clear=True), \
             patch.object(release, 'VARIANTS', {'cuda': ['amd64', 'arm64'], 'rocm': ['amd64']}), \
             patch.object(release.subprocess, 'run', side_effect=restore), \
             patch.object(release.subprocess, 'check_output', return_value=raw), \
             patch.object(release, 'ensure', side_effect=lambda ref, digest, publish: events.append(('fixed', ref))), \
             patch.object(release, 'layout', side_effect=layout), patch.object(release, 'copy_image', side_effect=copy):
            release.publish(Path('/unused'), from_registry=True)

        self.assertEqual(restored, ['cuda-amd64', 'cuda-arm64', 'rocm-amd64'])
        self.assertEqual(len(roots), 2)
        self.assertEqual([event[0] for event in events], ['fixed'] * 4 + ['latest'] * 4)
        self.assertTrue(all(not root.exists() for root in roots))

    def test_missing_registry_archive_stops_publishing_and_cleans_downloads(self):
        roots = []

        def restore(args, check):
            archive = Path(args[-1])
            roots.append(archive.parents[1])
            archive.parent.mkdir(parents=True)

        with patch.dict(release.os.environ, self.env(), clear=True), \
             patch.object(release.subprocess, 'run', side_effect=restore), patch.object(release, 'ensure') as publish:
            with self.assertRaisesRegex(ValueError, 'Missing immutable registry archive'):
                release.publish(Path('/unused'), from_registry=True)
        publish.assert_not_called()
        self.assertTrue(all(not root.exists() for root in roots))

    def test_failed_restore_cleans_downloads_and_never_rebuilds(self):
        roots = []

        def restore(args, check):
            archive = Path(args[-1])
            roots.append(archive.parents[1])
            archive.parent.mkdir(parents=True)
            archive.write_bytes(b'interrupted download')
            raise subprocess.CalledProcessError(1, args)

        with patch.dict(release.os.environ, self.env(), clear=True), \
             patch.object(release.subprocess, 'run', side_effect=restore) as commands, \
             patch.object(release, 'ensure') as publish:
            with self.assertRaises(subprocess.CalledProcessError):
                release.publish(Path('/unused'), from_registry=True)
        commands.assert_called_once()
        publish.assert_not_called()
        self.assertTrue(all(not root.exists() for root in roots))

    def test_later_runtime_failure_does_not_update_any_latest(self):
        raw = json.dumps({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json'}).encode()
        fixed = []

        def ensure(ref, digest, publish):
            fixed.append(ref)
            if ref.endswith('-rocm'):
                raise RuntimeError('later runtime failed')

        with patch.dict(release.os.environ, self.env(), clear=True), \
             patch.object(release, 'VARIANTS', {'cuda': ['amd64'], 'rocm': ['amd64']}), \
             patch.object(release.subprocess, 'check_output', return_value=raw), \
             patch.object(release, 'ensure', side_effect=ensure), patch.object(release, 'layout'), \
             patch.object(release, 'copy_image') as copy:
            with self.assertRaisesRegex(RuntimeError, 'later runtime failed'):
                release.publish(Path('/unused'))
        self.assertTrue(any(ref.endswith('-cuda') for ref in fixed))
        copy.assert_not_called()

    def test_stalled_copy_is_killed_and_reports_its_destination(self):
        original_popen = subprocess.Popen
        processes = []

        def spawn(command):
            process = original_popen([sys.executable, '-c', 'import time; time.sleep(60)'])
            processes.append(process)
            return process

        with patch.object(release.subprocess, 'Popen', side_effect=spawn), \
             patch.object(release, 'COPY_TIMEOUT_SECONDS', 0.02), \
             patch.object(release, 'PROGRESS_INTERVAL_SECONDS', 0.5), patch('builtins.print') as log:
            with self.assertRaisesRegex(RuntimeError, 'timed out.*registry/image:1.0.0'):
                release.copy_image('oci:/checked:release', 'docker://registry/image:1.0.0')
        self.assertIsNotNone(processes[0].poll())
        self.assertTrue(any('Still copying registry/image:1.0.0' in str(call) for call in log.call_args_list))
        self.assertFalse(any('Copied registry/image' in str(call) for call in log.call_args_list))

    def test_failed_copy_does_not_report_success(self):
        original_popen = subprocess.Popen

        def spawn(command):
            return original_popen([sys.executable, '-c', 'raise SystemExit(7)'])

        with patch.object(release.subprocess, 'Popen', side_effect=spawn), patch('builtins.print') as log:
            with self.assertRaises(subprocess.CalledProcessError) as caught:
                release.copy_image('oci:/checked:release', 'docker://registry/image:1.0.0')
        self.assertEqual(caught.exception.returncode, 7)
        self.assertFalse(any('Copied registry/image' in str(call) for call in log.call_args_list))

    def test_remote_inspection_timeout_never_creates_a_tag(self):
        with patch.object(release.subprocess, 'run', side_effect=subprocess.TimeoutExpired(['skopeo'], 120)), \
             patch.object(release, 'copy_image') as copy:
            with self.assertRaisesRegex(RuntimeError, 'Registry inspection timed out.*registry/image:1.0.0'):
                release.ensure('registry/image:1.0.0', 'sha256:expected', copy)
        copy.assert_not_called()

    def test_oci_layout_preserves_blob_bytes_and_deterministic_index(self):
        raw = release.manifest([])
        data = b'checked image content'
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'cuda-amd64'
            source.mkdir()
            with tarfile.open(source / 'image.tar', 'w') as archive:
                member = tarfile.TarInfo('blobs/sha256/' + digest)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            release.layout(root, 'cuda', raw, root / 'layout')
            self.assertEqual((root / 'layout/blobs/sha256' / digest).read_bytes(), data)
            index = json.loads((root / 'layout/index.json').read_text())
            descriptor = index['manifests'][0]
            self.assertEqual(descriptor['digest'], 'sha256:' + hashlib.sha256(raw).hexdigest())
            self.assertEqual((root / 'layout/blobs/sha256' / descriptor['digest'].split(':')[1]).read_bytes(), raw)

    def test_four_runtime_release_has_only_eight_tags_per_registry_and_is_retryable(self):
        registries, copies, raw_manifests = {}, [], {}

        def copy(source, destination):
            if source.startswith('oci:'):
                folder = Path(source[4:].removesuffix(':release'))
                root = json.loads((folder/'index.json').read_bytes())['manifests'][0]
                raw = (folder/'blobs/sha256'/root['digest'].split(':')[1]).read_bytes()
                # The full graph is present even though no architecture tag exists.
                for entry in json.loads(raw)['manifests']:
                    data = (folder/'blobs/sha256'/entry['digest'].split(':')[1]).read_bytes()
                    self.assertEqual('sha256:'+hashlib.sha256(data).hexdigest(), entry['digest'])
            else:
                raw = registries[source.removeprefix('docker://')]
            registries[destination.removeprefix('docker://')] = raw
            copies.append(destination)

        def inspect(command, **kwargs):
            return raw_manifests[Path(command[-1].removeprefix('oci-archive:')).parent.name]

        def digest(reference):
            raw = registries.get(reference)
            return 'sha256:'+hashlib.sha256(raw).hexdigest() if raw else None

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for variant, arches in release.VARIANTS.items():
                for arch in arches:
                    name = f'{variant}-{arch}'
                    folder = directory/name
                    folder.mkdir()
                    raw = json.dumps(dict(schemaVersion=2, mediaType='application/vnd.oci.image.manifest.v1+json',
                                          annotations={'fixture': name})).encode()
                    raw_manifests[name] = raw
                    with tarfile.open(folder/'image.tar', 'w') as archive:
                        member = tarfile.TarInfo('blobs/sha256/'+hashlib.sha256(raw).hexdigest())
                        member.size = len(raw)
                        archive.addfile(member, io.BytesIO(raw))
            with patch.dict(release.os.environ, self.env(), clear=True), \
                 patch.object(release.subprocess, 'check_output', side_effect=inspect), \
                 patch.object(release, 'remote_digest', side_effect=digest), \
                 patch.object(release, 'copy_image', side_effect=copy):
                release.publish(directory)
                self.assertEqual(len(copies), 16)
                self.assertTrue(all(':latest-' not in ref for ref in copies[:8]))
                self.assertTrue(all(':latest-' in ref for ref in copies[8:]))
                expected = {f'{version}-{runtime}' for version in ('1.2.3', 'latest')
                            for runtime in ('cuda', 'rocm', 'openvino', 'rknn')}
                for prefix in release.config(self.env())[1]:
                    tags = {ref.split(':')[-1] for ref in registries if ref.startswith(prefix+'/')}
                    self.assertEqual(tags, expected)
                    for runtime, arch in [('cuda', 'amd64'), ('rocm', 'amd64'), ('openvino', 'amd64'), ('rknn', 'arm64')]:
                        fixed = registries[f'{prefix}/singverse-compute:1.2.3-{runtime}']
                        self.assertEqual(fixed, registries[f'{prefix}/singverse-compute:latest-{runtime}'])
                        self.assertEqual(json.loads(fixed)['manifests'][0]['platform'], {'os': 'linux', 'architecture': arch})
                release.publish(directory)
                self.assertEqual(len(copies), 24, 'Retries reuse all immutable tags and only refresh latest')


if __name__ == '__main__':
    unittest.main()
