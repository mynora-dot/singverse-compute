#!/usr/bin/env bash
# Free only disposable GitHub-hosted Linux runner tools; never run on a NAS.
set -euo pipefail
if [[ "${GITHUB_ACTIONS:-}" != true || "${RUNNER_ENVIRONMENT:-}" != github-hosted || "${RUNNER_OS:-}" != Linux ]]; then
  echo 'Disk cleanup requires a disposable GitHub-hosted Linux runner.' >&2
  exit 1
fi

echo 'Disk space before cleanup:'
df -h /
before=$(df --output=avail -B1 / | tail -n 1)
# These SDKs are not used by compute's Docker builds or registry publishing.
# Keep host Python, Node, Git, gh, Docker, Skopeo, and swap intact.
sudo rm -rf \
  /usr/local/lib/android /usr/share/dotnet /opt/ghc /usr/local/.ghcup \
  /usr/share/swift /usr/share/miniconda /usr/local/julia* /usr/lib/jvm \
  /opt/hostedtoolcache/CodeQL /usr/local/share/powershell \
  /usr/local/share/chromium /opt/google/chrome /opt/microsoft/msedge \
  /usr/local/aws-cli /usr/local/aws-sam-cli /usr/share/az* /opt/az \
  /opt/google-cloud-sdk /usr/lib/google-cloud-sdk /usr/local/share/vcpkg
docker image prune --all --force
after=$(df --output=avail -B1 / | tail -n 1)
echo "Freed $(( (after - before) / 1024 / 1024 )) MiB; disk space after cleanup:"
df -h /
