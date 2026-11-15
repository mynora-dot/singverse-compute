import unittest
from unittest.mock import patch

from singverse_compute.core import build_info


class BuildVersionTest(unittest.TestCase):
    def test_native_version_uses_the_actions_environment(self):
        with patch.dict(build_info.os.environ, {"SINGVERSE_VERSION": "7.8.9"}):
            self.assertEqual(build_info.native_version(), "7.8.9")
        with patch.dict(build_info.os.environ, {"SINGVERSE_VERSION": "7.8.10"}):
            self.assertEqual(build_info.native_version(), "7.8.10")
        with patch.dict(build_info.os.environ, {}, clear=True):
            self.assertEqual(build_info.native_version(), build_info.__version__)
