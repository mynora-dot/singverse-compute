SHELL := /bin/sh
.DEFAULT_GOAL := help
PYTHON ?= python3
COMPUTE_RUNTIME ?= cuda
COMPOSE = docker compose --project-directory . -f infra/docker/compose/compose.compute.yaml
.PHONY: help compute-setup compute-check compute-run docker-build docker-up docker-down docker-status docker-test docker-context-check

compute-setup:
	$(PYTHON) -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12+ required"'
	$(PYTHON) -m venv .venv
	.venv/bin/python -m pip install -r requirements.lock && .venv/bin/python -m pip install --no-deps --no-build-isolation -e .


transcode-fixtures:
	.venv/bin/python scripts/setup-transcode-fixtures.py


transcode-tools:
	.venv/bin/python scripts/setup-transcode-tools.py


.venv/.test-deps: requirements-test.lock requirements.lock
	.venv/bin/python -m pip install -r requirements-test.lock
	touch $@

compute-check: .venv/.test-deps
	PATH="$(CURDIR)/.tools/jellyfin-ffmpeg/bin:$(CURDIR)/.tools/jellyfin-ffmpeg:$$PATH" .venv/bin/python -m unittest discover -s tests -v


compute-audio-setup:
	.venv/bin/python -m pip install -r requirements-audio.lock
	.venv/bin/python scripts/setup-audio-model.py


compute-fast-audio-setup:
	.venv/bin/python -m pip install -r requirements-audio.lock
	.venv/bin/python scripts/setup-fast-audio-model.py


macos-audio-check:
	.venv/bin/python -c "import platform; assert platform.system() == 'Darwin', 'Run on native macOS with Apple GPU access'"
	PATH="$(CURDIR)/.tools/jellyfin-ffmpeg/bin:$(CURDIR)/.tools/jellyfin-ffmpeg:$$PATH" SINGVERSE_TEST_MPS_MODEL_REPO="$(CURDIR)/.tools/demucs-htdemucs" .venv/bin/python -m unittest discover -s tests -p test_mps_audio.py -v


compute-audio-cuda-setup:
	.venv/bin/python -c "import platform; assert platform.system() == 'Linux' and platform.machine() == 'x86_64', 'CUDA package targets Linux x86_64'"
	.venv/bin/python -m pip install -r requirements-audio-cuda.lock


compute-audio-rocm-setup:
	.venv/bin/python -c "import platform,sys; assert platform.system() == 'Linux' and platform.machine() == 'x86_64' and sys.version_info[:2] == (3,12), 'ROCm package targets Linux x86_64 / Python 3.12'"
	.venv/bin/python -m pip install --no-build-isolation -r requirements-audio-rocm.lock
	.venv/bin/python -m pip check


compute-audio-rocm-native-setup:
	.venv/bin/python -c "import platform,sys; assert platform.system() == 'Linux' and platform.machine() == 'x86_64' and sys.version_info[:2] == (3,12), 'Native gfx1103 runtime targets Linux x86_64 / Python 3.12'"
	.venv/bin/python -m pip install --no-build-isolation -r requirements-audio-rocm-native.lock
	.venv/bin/python -m pip check


compute-audio-openvino-setup:
	.venv/bin/python -c "import platform; assert platform.system() == 'Linux' and platform.machine() == 'x86_64', 'OpenVINO image targets Linux x86_64'"
	.venv/bin/python -m pip install -r requirements-audio-openvino.lock


compute-run:
	PATH="$(CURDIR)/.tools/jellyfin-ffmpeg/bin:$(CURDIR)/.tools/jellyfin-ffmpeg:$$PATH" .venv/bin/python -m singverse_compute


compute-audio-rknn-setup:
	.venv/bin/python -c "import platform; assert platform.system() == 'Linux' and platform.machine() == 'aarch64', 'RKNN requires native Linux aarch64'"
	.venv/bin/python -m pip install -r requirements-audio-rknn.lock


compute-audio-uvr-setup:
	.venv/bin/python -m pip install -r requirements-audio-uvr.lock
	.venv/bin/python scripts/setup-uvr-model.py


compute-separation-cpu-setup:
	.venv/bin/python scripts/setup-separation-runtime.py


compute-separation-mlx-setup:
	.venv/bin/python -c "import platform; assert platform.system() == 'Darwin' and platform.machine() == 'arm64', 'Run on native Apple Silicon'"
	.venv/bin/python scripts/setup-separation-runtime.py --directory .venv-audio-mlx --lock requirements-separation-mlx.lock --role accelerator


compute-images:
	COMPUTE_RUNTIME="$(COMPUTE_RUNTIME)" docker compose --project-directory . -f infra/docker/compose/compose.compute.yaml -f infra/docker/compose/compose.compute-build.yaml build


docker-build:
	$(COMPOSE) -f infra/docker/compose/compose.compute-build.yaml build

docker-up:
	$(COMPOSE) up -d --wait --wait-timeout 180

docker-down:
	$(COMPOSE) down

docker-status:
	$(COMPOSE) ps

docker-test:
	$(PYTHON) -m unittest discover -s infra/docker/tests -v

docker-context-check:
	$(PYTHON) infra/docker/check-context.py

help:
	@printf '%s\n' 'compute-setup compute-check compute-run transcode-tools transcode-fixtures' 'compute-audio-setup compute-fast-audio-setup compute-audio-cuda-setup compute-audio-rocm-setup compute-audio-rocm-native-setup compute-audio-openvino-setup compute-audio-rknn-setup compute-audio-uvr-setup' 'compute-separation-cpu-setup compute-separation-mlx-setup macos-audio-check' 'docker-build docker-up docker-down docker-status docker-test docker-context-check'
