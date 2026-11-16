"""Explicit audio runtime profiles; importing this module never initializes Torch/GPU."""
from importlib.metadata import PackageNotFoundError, version

NATIVE_ROCM_COMMON_PACKAGES = {
    'torch': '2.11.0+rocm10.0.0',
    'torchaudio': '2.11.0+rocm10.0.0',
    'rocm': '10.0.0',
    'rocm-bootstrap': '0.3.0',
    'rocm-sdk-core': '10.0.0',
    'rocm-sdk-libraries': '10.0.0',
    'triton': '3.8.0+git4cff872c.rocm10.0.0',
}
NATIVE_ROCM_DEVICE_PACKAGES = {
    architecture: {
        'rocm-sdk-device-' + architecture: '10.0.0',
        'amd-torch-device-' + architecture: '2.11.0+rocm10.0.0',
        **({'amd-torch-device-gfx110x': '2.11.0+rocm10.0.0'} if architecture.startswith('gfx110') else {}),
    }
    for architecture in ('gfx908', 'gfx90a', 'gfx942', 'gfx1030', 'gfx1100', 'gfx1101', 'gfx1102', 'gfx1103')
}
# The original single-device lock remains usable for native gfx1103 installations.
NATIVE_ROCM_PACKAGES = {**NATIVE_ROCM_COMMON_PACKAGES, **NATIVE_ROCM_DEVICE_PACKAGES['gfx1103']}


def _matches(packages):
    try:
        return all(version(name) == expected for name, expected in packages.items())
    except PackageNotFoundError:
        return False


def native_rocm_architectures():
    """Installed, version-matched device packs; this does not initialize the GPU."""
    if not _matches(NATIVE_ROCM_COMMON_PACKAGES):
        return ()
    return tuple(architecture for architecture, packages in NATIVE_ROCM_DEVICE_PACKAGES.items()
                 if _matches(packages))


def audio_runtime_profile():
    try:
        if version('demucs') != '4.0.1':
            return None
        if all(version(name).split('+')[0] == '2.5.1' for name in ('torch', 'torchaudio')):
            return 'legacy'
        if native_rocm_architectures():
            return 'rocm-native'
    except PackageNotFoundError:
        pass
    return None
