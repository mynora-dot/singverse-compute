#!/usr/bin/env python3
"""Verify the real BuildKit COPY context with isolated credential fixtures."""
from pathlib import Path
import shutil
import subprocess
import tempfile
ROOT = Path(__file__).resolve().parents[2]
if (ROOT / ".dockerignore").read_bytes() != (ROOT / "infra/docker/build/.dockerignore").read_bytes():
    raise SystemExit("Root .dockerignore differs from maintained source")
with tempfile.TemporaryDirectory(prefix="singverse-compute-context-") as directory:
    work = Path(directory)
    context, output = work / "context", work / "output"
    context.mkdir()
    shutil.copy2(ROOT / ".dockerignore", context / ".dockerignore")
    fixtures = {
        "requirements.lock": "locked",
        "singverse_compute/core/config.py": "source",
        "infra/docker/build/compute/install-opencl.sh": "source",
        "infra/docker/build/compute/share-cuda-libraries.py": "source",
        ".env": "isolated-fixture", ".tools/private.pem": "isolated-fixture",
        ".venv/credential": "isolated-fixture", "data/test.db": "isolated-fixture",
        "singverse_compute/core/build.json": "isolated-fixture",
        "singverse_compute/__pycache__/module.pyc": "isolated-fixture",
        "singverse_compute/private.key": "isolated-fixture",
        "singverse_compute/.env": "isolated-fixture",
        "other-service/private.py": "isolated-fixture",
    }
    for name, content in fixtures.items():
        path = context / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    dockerfile = work / "Context.Dockerfile"
    dockerfile.write_text("FROM scratch\nCOPY . /\n")
    subprocess.run(["docker", "buildx", "build", "--file", str(dockerfile),
                    "--output", "type=local,dest=" + str(output), str(context)], check=True)
    actual = {str(path.relative_to(output)) for path in output.rglob("*") if path.is_file()}
    expected = {name for name, content in fixtures.items() if content != "isolated-fixture"}
    if actual != expected:
        raise SystemExit("Docker context has missing sources or unexpected files: " + repr(sorted(actual ^ expected)))
print("Compute Docker COPY context verified")
