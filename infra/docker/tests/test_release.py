import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
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
                    DOCKERHUB_NAMESPACE='custom-hub', ACR_REGISTRY='registry.example.com', ACR_NAMESPACE='custom-acr',
                    GHCR_TOKEN='test', DOCKERHUB_USERNAME='test', DOCKERHUB_TOKEN='test',
                    ACR_USERNAME='test', ACR_PASSWORD='test')

    def test_namespaces_are_independent_configuration(self):
        env = self.env()
        version, prefixes = release.config(env)
        self.assertEqual(version, '1.2.3')
        self.assertEqual(prefixes, ['ghcr.io/mynora-dot', 'docker.io/custom-hub', 'registry.example.com/custom-acr'])
        env['GITHUB_REPOSITORY_OWNER'] = 'MyNora-Dot'
        self.assertEqual(release.config(env)[1][0], 'ghcr.io/mynora-dot')
        env['GHCR_NAMESPACE'] = 'custom-ghcr'
        self.assertEqual(release.config(env)[1][0], 'ghcr.io/custom-ghcr')

    def test_missing_destination_or_secret_fails_before_publishing(self):
        for key in ('DOCKERHUB_NAMESPACE', 'ACR_REGISTRY', 'ACR_NAMESPACE', 'GHCR_TOKEN',
                    'DOCKERHUB_USERNAME', 'DOCKERHUB_TOKEN', 'ACR_USERNAME', 'ACR_PASSWORD'):
            env = self.env()
            del env[key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                release.config(env)

    def test_release_tag_and_registry_validation(self):
        for key, value in [('RELEASE_TAG', 'v9.0.0-rc.1'), ('ACR_REGISTRY', 'https://registry.example.com'),
                           ('DOCKERHUB_NAMESPACE', 'UpperCase'), ('ACR_NAMESPACE', 'a/b')]:
            env = self.env()
            env[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                release.config(env)

    def test_existing_equal_tag_is_retryable_and_different_tag_is_never_written(self):
        for existing in ('sha256:equal', 'sha256:different'):
            with patch.object(release, 'remote_digest', return_value=existing), patch.object(release, 'run') as publish:
                if existing.endswith('different'):
                    with self.assertRaises(ValueError):
                        release.ensure('registry/image:1.0.0', 'sha256:equal', publish)
                else:
                    release.ensure('registry/image:1.0.0', 'sha256:equal', publish)
                publish.assert_not_called()

    def test_absent_tag_is_verified_after_copy(self):
        with patch.object(release, 'remote_digest', side_effect=[None, 'sha256:expected']), patch.object(release, 'run') as copy:
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
            if ref.startswith('registry.example.com/'):
                raise RuntimeError('destination unavailable')
        with patch.dict(release.os.environ, self.env(), clear=True), patch.object(release, 'VARIANTS', {'base': ['amd64']}), \
             patch.object(release.subprocess, 'check_output', return_value=raw), patch.object(release, 'ensure', side_effect=ensure):
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
            if args[-1].endswith(':latest-base'):
                events.append(('latest', args[-1]))
        with patch.dict(release.os.environ, self.env(), clear=True), patch.object(release, 'VARIANTS', {'base': ['amd64']}), \
             patch.object(release.subprocess, 'check_output', return_value=raw), patch.object(release, 'ensure', side_effect=ensure), \
             patch.object(release, 'layout'), patch.object(release, 'run', side_effect=copy):
            release.publish(Path('/unused'))
        self.assertEqual([event[0] for event in events], ['fixed'] * 6 + ['latest'] * 3)
        self.assertEqual(len({event[2] for event in events[3:6]}), 1)
        self.assertTrue(all(event[1].startswith('docker://') for event in events[-3:]))
        self.assertEqual(len({event[1] for event in events[-3:]}), 3)

    def test_version_index_failure_does_not_update_latest(self):
        raw = json.dumps({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json'}).encode()
        def ensure(ref, digest, publish):
            if not ref.endswith('-amd64'):
                raise RuntimeError('index publication failed')
        with patch.dict(release.os.environ, self.env(), clear=True), patch.object(release, 'VARIANTS', {'base': ['amd64']}), \
             patch.object(release.subprocess, 'check_output', return_value=raw), patch.object(release, 'ensure', side_effect=ensure), \
             patch.object(release, 'layout'), patch.object(release, 'run') as copy:
            with self.assertRaises(RuntimeError):
                release.publish(Path('/unused'))
            copy.assert_not_called()

    def test_release_archives_are_bounded_to_one_runtime_and_cleaned_before_latest(self):
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
             patch.object(release, 'VARIANTS', {'base': ['amd64', 'arm64'], 'cuda': ['amd64']}), \
             patch.object(release.subprocess, 'run', side_effect=restore), \
             patch.object(release.subprocess, 'check_output', return_value=raw), \
             patch.object(release, 'ensure', side_effect=lambda ref, digest, publish: events.append(('fixed', ref))), \
             patch.object(release, 'layout', side_effect=layout), patch.object(release, 'run', side_effect=copy):
            release.publish(Path('/unused'), from_release=True)

        self.assertEqual(restored, ['base-amd64', 'base-arm64', 'cuda-amd64'])
        self.assertEqual(len(roots), 2)
        self.assertEqual([event[0] for event in events], ['fixed'] * 15 + ['latest'] * 6)
        self.assertTrue(all(not root.exists() for root in roots))

    def test_missing_release_archive_stops_publishing_and_cleans_downloads(self):
        roots = []

        def restore(args, check):
            archive = Path(args[-1])
            roots.append(archive.parents[1])
            archive.parent.mkdir(parents=True)

        with patch.dict(release.os.environ, self.env(), clear=True), \
             patch.object(release.subprocess, 'run', side_effect=restore), patch.object(release, 'ensure') as publish:
            with self.assertRaisesRegex(ValueError, 'Missing immutable Release archive'):
                release.publish(Path('/unused'), from_release=True)
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
                release.publish(Path('/unused'), from_release=True)
        commands.assert_called_once()
        publish.assert_not_called()
        self.assertTrue(all(not root.exists() for root in roots))

    def test_later_runtime_failure_does_not_update_any_latest(self):
        raw = json.dumps({'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.manifest.v1+json'}).encode()
        fixed = []

        def ensure(ref, digest, publish):
            fixed.append(ref)
            if ref.endswith('-cuda'):
                raise RuntimeError('later runtime failed')

        with patch.dict(release.os.environ, self.env(), clear=True), \
             patch.object(release, 'VARIANTS', {'base': ['amd64'], 'cuda': ['amd64']}), \
             patch.object(release.subprocess, 'check_output', return_value=raw), \
             patch.object(release, 'ensure', side_effect=ensure), patch.object(release, 'layout'), \
             patch.object(release, 'run') as copy:
            with self.assertRaisesRegex(RuntimeError, 'later runtime failed'):
                release.publish(Path('/unused'))
        self.assertTrue(any(ref.endswith('-base') for ref in fixed))
        copy.assert_not_called()

    def test_oci_layout_preserves_blob_bytes_and_deterministic_index(self):
        raw = release.manifest([])
        data = b'checked image content'
        digest = hashlib.sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'base-amd64'
            source.mkdir()
            with tarfile.open(source / 'image.tar', 'w') as archive:
                member = tarfile.TarInfo('blobs/sha256/' + digest)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            with patch.object(release, 'VARIANTS', {'base': ['amd64']}):
                release.layout(root, 'base', raw, root / 'layout')
            self.assertEqual((root / 'layout/blobs/sha256' / digest).read_bytes(), data)
            index = json.loads((root / 'layout/index.json').read_text())
            descriptor = index['manifests'][0]
            self.assertEqual(descriptor['digest'], 'sha256:' + hashlib.sha256(raw).hexdigest())
            self.assertEqual((root / 'layout/blobs/sha256' / descriptor['digest'].split(':')[1]).read_bytes(), raw)


if __name__ == '__main__':
    unittest.main()
