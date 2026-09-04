# Lightweight Inference Image Implementation Plan

> **Status: CANCELLED (2026-07-16).** 用户决定停止拆分推理镜像并继续使用已验证的 `geoview-runtime:gpu-cu128`。本计划保留为历史实现与评审记录，不得继续执行构建、迁移或发布步骤；生产 Compose 已恢复旧镜像所需的宿主 `backend`/`docker` 只读挂载。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the NVIDIA inference Worker as a reproducible layered image without stale models, duplicated Torch layers, frontend/Node assets, Conda, or CUDA build tools, while moving the inference checkpoint to a verified read-only deployment artifact.

**Architecture:** One multi-target Dockerfile produces `inference-base`, `inference-core`, and `inference-worker`; only the final Worker container runs in production. The final image contains immutable runtime code and model definitions, while `model.inference.pth` is mounted separately and verified before model load.

**Tech Stack:** Docker BuildKit, Ubuntu 22.04, Python 3.10 venv, PyTorch 2.7.0+cu128, MMCV 2.1.0, MMEngine 0.10.4, official MMSeg 1.2.2 in `inference-core`, vendored MMSeg 1.1.2 in the final Worker, Flask-SQLAlchemy, Docker Compose, Python unittest.

---

## File map

- Create `backend/applications/inference/app.py`: minimal Worker-only Flask/SQLAlchemy application factory.
- Create `docker/requirements/inference-core.txt`: exact inference and geospatial runtime versions.
- Create `docker/requirements/inference-worker.txt`: exact Worker/database runtime versions.
- Create `docker/Dockerfile.inference-gpu.dockerignore`: narrow project-root build context.
- Rewrite `docker/Dockerfile.inference-gpu`: named base/core/worker targets and MMCV builder.
- Create `docker/check-inference-image.py`: machine-readable image-content contract check.
- Create `docker/build-inference-image.ps1`: deterministic Windows build/verification entrypoint.
- Create `docker/build-inference-image.sh`: deterministic Linux build/verification entrypoint.
- Create `docker/offline_bundle_manifest.py`: build and verify the complete offline manifest.
- Modify `backend/applications/interface/mmseg_inference_caller.py`: production checkpoint contract.
- Modify `backend/applications/inference/worker.py`: production model loader requires inference checkpoint.
- Modify `backend/run_inference_worker.py`: use Worker-only app factory.
- Modify `docker-compose.prod.yml`: immutable Worker code and read-only checkpoint mount.
- Modify `docker/check-inference-runtime.py`: image build/model metadata.
- Modify `backend/test_inference_runner.py`: checkpoint and Worker loader tests.
- Create `backend/test_inference_worker_app.py`: minimal app-factory tests.
- Create `backend/test_inference_image_contract.py`: image contract and offline manifest unit tests.
- Modify deployment and progress documents after verification.

### Task 1: Enforce the production checkpoint contract

**Files:**
- Modify: `backend/applications/interface/mmseg_inference_caller.py`
- Modify: `backend/applications/inference/worker.py`
- Modify: `backend/test_inference_runner.py`

- [x] **Step 1: Add failing tests**

Add tests that patch both checkpoint paths to temporary missing files and assert production mode raises a stable error while development mode can still select the original checkpoint:

```python
def test_production_model_path_requires_inference_checkpoint(self):
    caller = load_caller_module()
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        caller.CUGRS_CONFIG = {
            **caller.CUGRS_CONFIG,
            "inference_checkpoint_path": str(root / "model.inference.pth"),
            "checkpoint_path": str(root / "model.pth"),
        }
        with self.assertRaisesRegex(FileNotFoundError, "MODEL_CHECKPOINT_MISSING"):
            caller.get_model_paths("cc-ln/CUGRS", require_inference_checkpoint=True)

def test_development_model_path_can_fall_back_to_training_checkpoint(self):
    caller = load_caller_module()
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        training = root / "model.pth"
        training.touch()
        caller.CUGRS_CONFIG = {
            **caller.CUGRS_CONFIG,
            "inference_checkpoint_path": str(root / "model.inference.pth"),
            "checkpoint_path": str(training),
        }
        _, checkpoint = caller.get_model_paths("cc-ln/CUGRS")
        self.assertEqual(Path(checkpoint), training)
```

- [x] **Step 2: Run the focused tests and confirm RED**

Run: `cd backend && python -m unittest test_inference_runner.TestInferenceRunner.test_production_model_path_requires_inference_checkpoint test_inference_runner.TestInferenceRunner.test_development_model_path_can_fall_back_to_training_checkpoint -v`

Expected: first test fails because `require_inference_checkpoint` is not accepted.

- [x] **Step 3: Implement the minimal path contract**

Use this signature and behavior:

```python
def get_model_paths(model_id: str, *, require_inference_checkpoint: bool = False) -> Tuple[str, str]:
    if model_id != "cc-ln/CUGRS":
        raise ValueError(f"Unknown MMSeg model: {model_id}")
    config = os.path.abspath(CUGRS_CONFIG["config_path"])
    inference_checkpoint = os.path.abspath(CUGRS_CONFIG["inference_checkpoint_path"])
    if os.path.isfile(inference_checkpoint):
        return config, inference_checkpoint
    if require_inference_checkpoint:
        raise FileNotFoundError(f"MODEL_CHECKPOINT_MISSING: {inference_checkpoint}")
    checkpoint = os.path.abspath(CUGRS_CONFIG["checkpoint_path"])
    if not os.path.isfile(checkpoint):
        raise FileNotFoundError(f"MODEL_CHECKPOINT_MISSING: {inference_checkpoint}")
    return config, checkpoint
```

Change `_default_model_loader` to call `get_model_paths("cc-ln/CUGRS", require_inference_checkpoint=True)`.

- [x] **Step 4: Run focused and full inference tests**

Run: `cd backend && python -m unittest test_inference_runner.py test_inference_device.py test_inference_jobs.py -v`

Expected: all runnable tests pass; dependency-based skips remain explicitly reported only on the host environment.

- [x] **Step 5: Record checkpoint**

Record `feat: require inference checkpoint in production worker` in the progress file. If Git metadata has been restored, commit only the three files with that message; otherwise do not initialize a replacement repository.

### Task 2: Add a minimal Worker application factory

**Files:**
- Create: `backend/applications/inference/app.py`
- Modify: `backend/run_inference_worker.py`
- Create: `backend/test_inference_worker_app.py`

- [x] **Step 1: Write the failing factory test**

```python
class TestInferenceWorkerApp(unittest.TestCase):
    def test_factory_initializes_only_database_runtime(self):
        with patch.dict(os.environ, {"FLASK_CONFIG": "testing"}):
            from applications.inference.app import create_worker_app
            app = create_worker_app()
        self.assertEqual(app.config["SQLALCHEMY_DATABASE_URI"], "sqlite:///:memory:")
        self.assertEqual({rule.endpoint for rule in app.url_map.iter_rules()}, {"static"})
```

- [x] **Step 2: Confirm RED**

Run: `cd backend && python -m unittest test_inference_worker_app.py -v`

Expected: import fails because `applications.inference.app` does not exist.

- [x] **Step 3: Implement the Worker-only factory**

```python
import os
from flask import Flask
from applications.configs import config
from applications.extensions.init_sqlalchemy import db

def create_worker_app(config_name=None):
    selected = config_name or os.getenv("FLASK_CONFIG", "development")
    app = Flask(__name__)
    app.config.from_object(config[selected])
    db.init_app(app)
    from applications.models.inference_job import InferenceJob, InferenceWorkerState
    with app.app_context():
        db.create_all()
    return app
```

Change `run_inference_worker.py` to import and call `create_worker_app`; preserve signal handling, configuration parsing, and the single-Worker guard.

- [x] **Step 4: Verify the factory and entrypoint**

Run: `cd backend && python -m unittest test_inference_worker_app.py test_inference_runner.py -v`

Run: `python -m py_compile backend/run_inference_worker.py backend/applications/inference/app.py`

Expected: PASS with no API blueprints registered by the Worker app.

- [x] **Step 5: Record checkpoint**

Record `refactor: isolate inference worker application runtime`; commit only these files if Git is available.

### Task 3: Lock the inference dependencies and build context

**Files:**
- Create: `docker/requirements/inference-core.txt`
- Create: `docker/requirements/inference-worker.txt`
- Create: `docker/Dockerfile.inference-gpu.dockerignore`
- Create: `backend/test_inference_image_contract.py`

- [x] **Step 1: Write a failing context-contract test**

The test must assert the Dockerfile-specific ignore file excludes `*.pth`, frontend, miner, tests, docs, training data and wheels while re-including backend runtime source and docker scripts.

```python
def test_inference_context_excludes_large_non_runtime_assets(self):
    text = (ROOT / "docker" / "Dockerfile.inference-gpu.dockerignore").read_text("utf-8")
    for pattern in ("**/*.pth", "frontend/", "miner/", "**/tests/", "**/docs/", "**/*.whl"):
        self.assertIn(pattern, text)
    self.assertIn("!backend/", text)
    self.assertIn("!docker/", text)
```

- [x] **Step 2: Confirm RED**

Run: `cd backend && python -m unittest test_inference_image_contract.py -v`

Expected: FAIL because the ignore file and lock files do not exist.

- [x] **Step 3: Create exact lock files**

`inference-core.txt` must pin the observed compatible set:

```text
mmengine==0.10.4
mmsegmentation==1.2.2
numpy==1.26.4
opencv-python-headless==4.10.0.84
rasterio==1.4.4
shapely==1.8.5.post1
Pillow>=9.2.0,<12
PyYAML>=6.0,<7
```

Torch/torchvision and the locally built MMCV wheel remain explicit Dockerfile installs because they use separate indexes/build stages.

The core lock uses official `mmsegmentation==1.2.2`, whose `MMCV_MAX=2.2.0` covers MMCV 2.1.0. The final Worker intentionally resolves the repository's vendored MMSeg 1.1.2 first through `PYTHONPATH`; its upper bound has been relaxed to `MMCV_MAX=2.2.0` (runtime constraint `<2.2.0`). Therefore core dependency checks expect 1.2.2, while the final Worker image checker continues to expect 1.1.2.

`inference-worker.txt` must contain:

```text
Flask==2.2.2
Flask-SQLAlchemy==2.5.1
SQLAlchemy==1.4.46
PyMySQL==1.2.0
Werkzeug==2.2.3
python-dotenv>=0.21.0,<2
```

Create an allowlist-first Dockerfile-specific ignore file: ignore everything, re-include `backend/`, `docker/`, and `config.yaml`, then exclude all checkpoints, wheels, tests, docs, demos, results, training data, caches and frontend/miner directories.

- [x] **Step 4: Verify context size and tests**

Run: `docker buildx build --check -f docker/Dockerfile.inference-gpu .`

Run: `cd backend && python -m unittest test_inference_image_contract.py -v`

Expected: context excludes `model.pth` and `model.inference.pth`; Dockerfile check and unit test pass.

- [x] **Step 5: Record checkpoint**

Record `build: define minimal inference dependency boundary`; commit if Git is available.

### Task 4: Rebuild the three-target Dockerfile

**Files:**
- Rewrite: `docker/Dockerfile.inference-gpu`
- Create: `docker/check-inference-image.py`
- Modify: `backend/test_inference_image_contract.py`

- [x] **Step 1: Add failing source-contract tests**

Assert the Dockerfile contains named `inference-base`, `mmcv-builder`, `inference-core`, and `inference-worker` stages; starts from CUDA base/devel images; creates `/opt/venv`; never copies frontend/miner/checkpoints; and runs the image contract checker.

- [x] **Step 2: Confirm RED**

Run: `cd backend && python -m unittest test_inference_image_contract.py -v`

Expected: FAIL because the current Dockerfile derives from the bloated application image and uses Conda.

- [x] **Step 3: Implement the multi-target build**

The Dockerfile must:

1. derive runtime from `nvidia/cuda:12.8.0-base-ubuntu22.04`;
2. install Python 3.10, venv, GDAL runtime and required shared libraries with `--no-install-recommends`;
3. build MMCV in `nvidia/cuda:12.8.0-devel-ubuntu22.04` against Torch 2.7.0+cu128;
4. cache the wheel under a key containing Python 3.10, Torch 2.7.0, CUDA 12.8, MMCV 2.1.0 and the architecture list;
5. install runtime packages into `/opt/venv`;
6. copy only the allowlisted backend/docker source into the final stage;
7. create a non-root user, runtime directories and a healthcheck;
8. run `check-inference-image.py` during build.

The final stage must use `ENTRYPOINT ["/bin/bash", "/app/docker/start-inference-worker.sh"]` and must not contain `/usr/local/cuda/bin/nvcc`.

- [x] **Step 4: Implement the image checker**

`check-inference-image.py` prints JSON and exits nonzero if forbidden paths exist. Its report contains Python/Torch/CUDA/MMCV/MMEngine/MMSeg versions, whether `nvcc` exists, and a list of forbidden assets found.

- [ ] **Step 5: Build targets from an empty logical cache**

Run from repository root:

```powershell
docker build --progress=plain -f docker/Dockerfile.inference-gpu --target inference-base -t geoview-inference-base:py310-cu128 .
docker build --progress=plain -f docker/Dockerfile.inference-gpu --target inference-core -t geoview-inference-core:mmcv210-cu128 .
docker build --progress=plain -f docker/Dockerfile.inference-gpu --target inference-worker -t geoview-inference-worker:candidate .
```

Expected: all builds pass; the final build context does not transfer checkpoint files.

- [ ] **Step 6: Verify image content**

Run: `docker run --rm --entrypoint python geoview-inference-worker:candidate /app/docker/check-inference-image.py`

Expected: `ok=true`, `nvcc=false`, `forbidden_assets=[]`.

- [ ] **Step 7: Record checkpoint**

Record `build: add layered lightweight inference image`; commit Docker and test files if Git is available.

### Task 5: Make production Compose use immutable code and a model mount

**Files:**
- Modify: `docker-compose.prod.yml`
- Create: `docker-compose.inference-dev.yml`
- Modify: `docker/check-inference-runtime.py`
- Modify: `backend/test_inference_image_contract.py`

- [x] **Step 1: Add failing Compose assertions**

Parse `docker compose ... config --format json` and assert production Worker does not bind `./backend`, mounts `model.inference.pth` read-only at the fixed checkpoint path, and the development override alone binds backend source.

- [x] **Step 2: Confirm RED**

Run the Compose assertion test with verification-only credentials.

Expected: FAIL because production currently mounts the whole backend.

- [x] **Step 3: Modify production mounts**

Remove `./backend:/app/backend:ro` from `inference-worker`. Add explicit read-only mounts for the inference checkpoint, configured raster input directory and KML; preserve named volumes for uploads, outputs and job runtime. Keep the existing Web backend mounts unchanged.

Create `docker-compose.inference-dev.yml` that restores `./backend:/app/backend:ro` only for local debugging.

- [x] **Step 4: Extend runtime self-check**

Add `image_build`, `checkpoint_path`, `checkpoint_size`, and `checkpoint_sha256` fields. Hash in 8 MiB chunks and never print other input paths or credentials.

- [x] **Step 5: Verify both Compose modes**

Run base, GPU, and development-override Compose config assertions.

Expected: only GPU override grants NVIDIA devices; only development override binds all backend source; checkpoint is read-only in production.

- [x] **Step 6: Record checkpoint**

Record `deploy: mount verified inference model separately`; commit if Git is available.

### Task 6: Add deterministic build and complete offline manifest tooling

**Files:**
- Create: `docker/build-inference-image.ps1`
- Create: `docker/build-inference-image.sh`
- Create: `docker/offline_bundle_manifest.py`
- Modify: `backend/test_inference_image_contract.py`
- Modify: `image_bundle.env`

- [x] **Step 1: Add failing manifest tests**

Use a temporary directory with fake image tar/model/config files. Assert the manifest contains relative path, byte count and lowercase SHA256 for every required artifact, and exits with `OFFLINE_ARTIFACT_MISSING` if any required artifact is absent.

- [x] **Step 2: Confirm RED**

Run: `cd backend && python -m unittest test_inference_image_contract.py -v`

Expected: FAIL because manifest tooling is absent.

- [x] **Step 3: Implement build wrappers**

Both scripts must build the final target from repository root, run image/content/runtime checks, inspect image IDs and sizes, and tag the canonical image only after verification. They must not delete existing images or caches.

- [x] **Step 4: Implement manifest generation**

Required artifact keys are `app_image_tar`, `inference_image_tar`, `mysql_image_tar`, `inference_checkpoint`, `compose_prod`, `compose_gpu`, and `config_template`. Optional entries cover maps and volume backups. Output deterministic UTF-8 JSON sorted by artifact key.

- [x] **Step 5: Update image bundle variables**

Add `INFERENCE_IMAGE` and `INFERENCE_IMAGE_TAR` without changing existing APP/MySQL variables.

- [x] **Step 6: Verify scripts without exporting the large tar**

Run PowerShell syntax validation, `bash -n`, unit tests, and manifest generation against temporary fixtures. Do not run `docker save` until explicitly selected as a release artifact.

- [x] **Step 7: Record checkpoint**

Record `build: add reproducible inference and offline manifest tooling`; commit if Git is available.

### Task 7: Full image acceptance and documentation

**Files:**
- Modify: `docs/inference_gpu_compatibility.md`
- Modify: `docs/offline_deployment_guide.md`
- Modify: `docs/system_guide.md`
- Modify: `docs/superpowers/progress/2026-07-15-nvidia-inference-remediation-progress.md`

- [ ] **Step 1: Capture candidate size evidence**

Run `docker image inspect` and `docker image ls`; record both content and expanded size. Run `du` for the largest ten final-image directories.

Expected: expanded image is at most 17.95 GB and target is at most 15 GB.

- [ ] **Step 2: Run CPU and GPU runtime acceptance**

Run image checker, no-GPU runtime self-check, GPU runtime self-check, actual Worker model initialization and 512×512 forward.

Expected: GPU uses `cuda:0`; no-GPU mode reports `GPU_NOT_VISIBLE` and falls back CPU; model SHA matches the mounted checksum.

- [ ] **Step 3: Run repository regression**

Run container backend tests, Miner tests, Node syntax check, both frontend builds, Python compilation and Compose assertions.

Expected: current 66 backend and 30 Miner tests remain passing; only documented pre-existing frontend size/deprecation warnings remain.

- [ ] **Step 4: Update documentation with measured values**

Replace the old 35.9 GB/current-image statements with both old and new measured values, exact image digest, layer targets, checkpoint mount, build commands and rollback tag.

- [ ] **Step 5: Preserve rollback image**

Tag the current verified image as `geoview-runtime:gpu-cu128-legacy` before assigning the canonical tag to the accepted candidate. Do not remove the legacy image.

- [ ] **Step 6: Record phase completion**

Record `release: accept lightweight NVIDIA inference image`; commit docs if Git is available. Do not start the pipeline performance phase until every acceptance item passes.
