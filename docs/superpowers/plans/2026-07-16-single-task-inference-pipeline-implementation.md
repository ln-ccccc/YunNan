# Single-Task Inference Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce one large KML ROI task's end-to-end latency by overlapping bounded CPU preparation and post-processing with a single resident GPU model, using measured stage timings and preserving exact FP32 output behavior.

**Architecture:** FID-level preparation runs in a small bounded process pool, the main Worker process owns the only GPU model and inference stream, and completed FIDs enter a bounded post-processing executor before atomic publication. Stage timers and an end-to-end benchmark decide whether each optimization, batch=2, or optional FP16 is retained.

**Tech Stack:** Python 3.10, concurrent.futures, multiprocessing spawn context, PyTorch/MMSeg, GDAL/rasterio, OpenCV, Flask-SQLAlchemy, unittest, Docker/NVIDIA runtime.

---

## File map

- Create `backend/applications/inference/timing.py`: deterministic stage timer and serialization.
- Create `backend/applications/inference/locks.py`: cross-process FID publication lock.
- Create `backend/applications/kml_roi/pipeline_executor.py`: bounded preprocessing and post-processing orchestration helpers.
- Create `backend/benchmark_inference_pipeline.py`: repeatable end-to-end benchmark CLI and JSON report.
- Create `backend/test_inference_pipeline.py`: timing, FID preparation, queue, cancellation, lock and orchestration tests.
- Modify `backend/applications/kml_roi/tiles.py`: single-FID preparation/publication primitives plus compatibility wrappers.
- Modify `backend/applications/kml_roi/pipeline.py`: staged orchestration and timing output.
- Modify `backend/applications/interface/mmseg_segmentation.py`: testable batch and precision execution.
- Modify `backend/applications/inference/worker.py`: pipeline configuration, batch OOM reduction and precision propagation.
- Modify `backend/applications/configs/config.py`: bounded performance configuration.
- Modify `docker-compose.prod.yml`: explicit conservative defaults.
- Modify `backend/applications/api/analysis.py`: remove old KML ROI route alias.
- Modify `frontend/src/api/upload.js`: use canonical asynchronous job endpoint.
- Modify relevant tests and documentation after acceptance.

### Task 1: Add stage timing without changing execution order

**Files:**
- Create: `backend/applications/inference/timing.py`
- Modify: `backend/applications/kml_roi/pipeline.py`
- Create: `backend/test_inference_pipeline.py`

- [ ] **Step 1: Write failing timer tests**

```python
class TestStageTimer(unittest.TestCase):
    def test_timer_serializes_named_stages_and_total(self):
        clock = iter([10.0, 10.5, 12.0, 12.0, 15.0, 16.0])
        timer = StageTimer(clock=lambda: next(clock))
        with timer.measure("prepare_tiles_seconds"):
            pass
        with timer.measure("gpu_inference_seconds"):
            pass
        timer.finish()
        self.assertEqual(timer.as_dict(), {
            "prepare_tiles_seconds": 1.5,
            "gpu_inference_seconds": 3.0,
            "total_seconds": 6.0,
        })
```

Add a pipeline test with mocked stages and assert `stage_timings` is an optional result field without changing `status`, FID lists, or output paths.

- [ ] **Step 2: Confirm RED**

Run: `cd backend && python -m unittest test_inference_pipeline.py -v`

Expected: import fails because `applications.inference.timing` does not exist.

- [ ] **Step 3: Implement the timer**

```python
class StageTimer:
    def __init__(self, clock=time.perf_counter):
        self._clock = clock
        self._started = clock()
        self._durations = {}
        self._total = None

    @contextmanager
    def measure(self, name):
        started = self._clock()
        try:
            yield
        finally:
            self._durations[name] = self._durations.get(name, 0.0) + self._clock() - started

    def finish(self):
        self._total = self._clock() - self._started

    def as_dict(self):
        result = {key: round(value, 6) for key, value in self._durations.items()}
        result["total_seconds"] = round(self._total, 6)
        return result
```

Wrap existing pipeline stages without reordering them.

- [ ] **Step 4: Verify no behavior change**

Run: `cd backend && python -m unittest test_inference_pipeline.py test_inference_runner.py test_inference_jobs.py -v`

Expected: PASS; only `stage_timings` and throughput counts are new.

- [ ] **Step 5: Record checkpoint**

Record `perf: measure inference pipeline stages`; commit if Git is available.

### Task 2: Create a repeatable end-to-end baseline harness

**Files:**
- Create: `backend/benchmark_inference_pipeline.py`
- Modify: `backend/test_inference_pipeline.py`
- Modify: `docs/superpowers/progress/2026-07-15-nvidia-inference-remediation-progress.md`

- [ ] **Step 1: Write failing CLI/aggregation tests**

Test argument validation, median/p95 calculation, environment capture, and refusal to benchmark missing inputs. Inject a fake pipeline runner returning fixed `stage_timings`.

- [ ] **Step 2: Confirm RED**

Run: `cd backend && python -m unittest test_inference_pipeline.py -v`

Expected: FAIL because the benchmark module is missing.

- [ ] **Step 3: Implement the benchmark CLI**

Required arguments are `--old-tif`, `--new-tif`, `--kml`, `--output-root`, `--device`, `--repeat`, `--warmup`, and optional `--limit`. The script writes one JSON document containing input file SHA256 values, image/runtime versions, GPU name, repeat timings, median, p95, stage medians, FID/tile counts, peak CUDA memory and process RSS.

The benchmark uses a fresh job workdir and output root per repeat and never overwrites production output.

- [ ] **Step 4: Run the serial baseline in the accepted lightweight image**

Run at least one warmup and three measured repeats against the same representative large TIF/KML/FID selection. Save the JSON under `backend/runtime/benchmarks/` and record its SHA256 in the progress document; do not commit large inputs or generated outputs.

- [ ] **Step 5: Record checkpoint**

Record `perf: add reproducible ROI benchmark`; commit code/tests/docs if Git is available.

### Task 3: Extract FID-level preparation and publication primitives

**Files:**
- Modify: `backend/applications/kml_roi/tiles.py`
- Modify: `backend/test_inference_pipeline.py`

- [ ] **Step 1: Write failing primitive tests**

Test `prepare_fid_tiles` with injected crop/PNG functions for zero, one and two successful variants. Test `distribute_fid_outputs` publishes only a complete FID and leaves existing output untouched on a failed staging operation.

Use this immutable result shape:

```python
@dataclass(frozen=True)
class PreparedFid:
    index: int
    fid: str
    file_names: tuple
    variant_specs: tuple
```

- [ ] **Step 2: Confirm RED**

Run: `cd backend && python -m unittest test_inference_pipeline.py -v`

Expected: FAIL because the FID-level functions and dataclass are absent.

- [ ] **Step 3: Implement single-FID functions**

Move one iteration of current `prepare_tiles` into `prepare_fid_tiles`; keep `prepare_tiles` as a serial compatibility wrapper that calls the new primitive in feature order.

Move one FID iteration of `distribute_outputs` into `distribute_fid_outputs`; keep `distribute_outputs` as a serial compatibility wrapper. Convert mutable geometry/spec dictionaries to plain picklable structures only at the process boundary.

- [ ] **Step 4: Verify exact compatibility**

Run the new tests plus `test_inference_jobs.py` and `test_spectral_indices.py` in the Docker environment.

Expected: serial wrapper output is byte-for-byte equivalent for masks/images and structurally equal for summaries.

- [ ] **Step 5: Record checkpoint**

Record `refactor: isolate FID pipeline units`; commit if Git is available.

### Task 4: Add bounded preprocessing concurrency

**Files:**
- Create: `backend/applications/kml_roi/pipeline_executor.py`
- Modify: `backend/applications/configs/config.py`
- Modify: `docker-compose.prod.yml`
- Modify: `backend/test_inference_pipeline.py`

- [ ] **Step 1: Write failing bounded-executor tests**

With a fake executor, assert no more than `workers + queue_capacity` preparations are submitted, results retain feature indices, cancellation stops new submissions, and exceptions become structured FID errors.

- [ ] **Step 2: Confirm RED**

Run: `cd backend && python -m unittest test_inference_pipeline.py -v`

Expected: FAIL because `iter_prepared_fids` is missing.

- [ ] **Step 3: Implement bounded submission**

`iter_prepared_fids(features, prepare_one, workers, queue_capacity, should_cancel)` uses `ProcessPoolExecutor` with spawn context. It initially submits at most the bound, waits for the next completed future, yields its result, then submits one replacement. On cancellation it cancels pending futures and shuts down without accepting new work.

Add validated configuration:

```text
INFERENCE_PREPROCESS_WORKERS=2
INFERENCE_PREPARED_QUEUE_SIZE=2
```

Both values accept 1-8; invalid or zero values fail startup instead of silently becoming unbounded.

- [ ] **Step 4: Verify unit behavior and process safety**

Run unit tests on Windows host using fake executor and in the Linux image using the real spawn executor with temporary raster fixtures.

Expected: bounded pending count, deterministic result indices, and no leaked child processes.

- [ ] **Step 5: Record checkpoint**

Record `perf: add bounded ROI preparation pool`; commit if Git is available.

### Task 5: Make GPU inference batch-aware without concurrent model access

**Files:**
- Modify: `backend/applications/interface/mmseg_segmentation.py`
- Modify: `backend/applications/inference/worker.py`
- Modify: `backend/applications/configs/config.py`
- Modify: `docker-compose.prod.yml`
- Modify: `backend/test_inference_runner.py`

- [ ] **Step 1: Write failing batch tests**

Inject `inference_model` and assert batch=1 preserves single-image calls, batch=2 passes a list, output order matches input order, a batch OOM retries each image at batch=1, and a batch=1 OOM remains visible to Worker CPU fallback.

- [ ] **Step 2: Confirm RED**

Run: `cd backend && python -m unittest test_inference_runner.py -v`

Expected: FAIL because batch size and injected inference call are unsupported.

- [ ] **Step 3: Implement minimal batching**

Add keyword-only `batch_size=1`, `precision="fp32"`, and `inference_runner=None` to `run_inference_with_model`. Accept only batch sizes 1 or 2. Group filenames without reordering; load arrays before the call; normalize a single result to a one-item list. If batch=2 raises CUDA OOM, clear CUDA cache and retry those two items independently.

Add configuration:

```text
INFERENCE_BATCH_SIZE=1
INFERENCE_PRECISION=fp32
```

The Worker is still the sole model owner; no thread/process receives the model.

- [ ] **Step 4: Verify inference semantics**

Run focused tests, then real GPU batch=1 and batch=2 golden-image comparisons.

Expected: batch=1 exactly matches existing masks; batch=2 is retained only if end-to-end benchmark improves.

- [ ] **Step 5: Record checkpoint**

Record `perf: add safe optional MMSeg micro-batching`; commit if Git is available.

### Task 6: Add bounded post-processing and publication locking

**Files:**
- Create: `backend/applications/inference/locks.py`
- Modify: `backend/applications/kml_roi/pipeline_executor.py`
- Modify: `backend/applications/kml_roi/tiles.py`
- Modify: `backend/applications/configs/config.py`
- Modify: `docker-compose.prod.yml`
- Modify: `backend/test_inference_pipeline.py`

- [ ] **Step 1: Write failing lock and executor tests**

Use two processes contending for the same temporary FID lock. Assert only one enters the critical section, timeout produces `OUTPUT_PUBLISH_LOCK_TIMEOUT`, different FIDs can publish concurrently, and staging failure leaves prior published files unchanged.

- [ ] **Step 2: Confirm RED**

Run: `cd backend && python -m unittest test_inference_pipeline.py -v`

Expected: FAIL because lock/post-processing executor is missing.

- [ ] **Step 3: Implement the cross-platform file lock**

Use `fcntl.flock(LOCK_EX | LOCK_NB)` on Linux and `msvcrt.locking(..., LK_NBLCK, 1)` on Windows, polling with a monotonic deadline. Lock files live under `<output_root>/.locks/<sha256(fid)>.lock`; never use raw FID as a path.

- [ ] **Step 4: Implement bounded post-processing**

`submit_postprocess` uses a `ThreadPoolExecutor` because OpenCV/GDAL work is native and output artifacts already exist on disk. Configure:

```text
INFERENCE_POSTPROCESS_WORKERS=2
INFERENCE_PREDICTED_QUEUE_SIZE=2
```

Apply the FID lock only around final staging publication and derived statistics update, not around GPU inference.

- [ ] **Step 5: Verify atomic publication**

Run lock tests, existing path traversal/output atomicity tests, and Linux-container process contention tests.

Expected: no partial FID publication and no raw FID lock paths.

- [ ] **Step 6: Record checkpoint**

Record `perf: overlap safe FID post-processing`; commit if Git is available.

### Task 7: Orchestrate the bounded three-stage pipeline

**Files:**
- Modify: `backend/applications/kml_roi/pipeline.py`
- Modify: `backend/applications/inference/worker.py`
- Modify: `backend/test_inference_pipeline.py`
- Modify: `backend/test_inference_runner.py`

- [ ] **Step 1: Write failing orchestration tests**

Use events and fake executors to prove preparation overlaps GPU inference, post-processing overlaps the next inference, only one GPU call is active, cancellation stops submissions, timeout is checked at every stage boundary, and the final FID lists are restored to feature order.

- [ ] **Step 2: Confirm RED**

Run focused tests and confirm the current serial pipeline cannot satisfy overlap assertions.

- [ ] **Step 3: Implement staged orchestration**

Replace the all-prepare/all-infer/all-publish sequence with:

```python
for prepared in iter_prepared_fids(...):
    prediction = infer_prepared_fid(prepared)
    submit_postprocess(prediction)
drain_postprocess_in_feature_order()
```

Keep preparation/post-processing bounds, model ownership, status derivation, workdir cleanup and atomic publication explicit. Store stage timings and per-stage queue high-water marks in the result.

- [ ] **Step 4: Preserve serial fallback**

`INFERENCE_PIPELINE_MODE=serial|overlap` selects the implementation; default remains `serial` until target benchmark acceptance. Both modes use the same primitives and result schema.

- [ ] **Step 5: Run correctness tests**

Run all inference unit/integration tests and compare serial vs overlap outputs on a fixed ROI.

Expected: identical FP32 files and summaries except timing/debug fields.

- [ ] **Step 6: Record checkpoint**

Record `perf: overlap CPU stages with single GPU inference`; commit if Git is available.

### Task 8: Remove the old KML ROI route alias

**Files:**
- Modify: `backend/applications/api/analysis.py`
- Modify: `frontend/src/api/upload.js`
- Modify: `backend/test_inference_jobs.py`
- Modify or create frontend API test using the repository's existing test setup.

- [ ] **Step 1: Write failing route migration tests**

Assert GeoView calls `/api/inference/jobs`; canonical creation returns 201; `/api/analysis/kml_roi_inference` returns 404 after migration; query/cancel remain unchanged.

- [ ] **Step 2: Confirm RED**

Run backend and frontend focused tests.

Expected: frontend still calls the old alias and backend still registers it.

- [ ] **Step 3: Migrate and delete**

Change `kmlRoiInfer` to `/api/inference/jobs`. Delete only the old route function and its `create_inference_job_api` import from `analysis.py`; do not alter other analysis endpoints.

- [ ] **Step 4: Verify both frontends**

Run backend job API tests, Miner 30-test suite, Miner build and GeoView build.

Expected: all production callers use asynchronous jobs; no `wait=true` or blocking route remains.

- [ ] **Step 5: Record checkpoint**

Record `refactor: remove synchronous KML ROI route alias`; commit if Git is available.

### Task 9: Evaluate FP16 independently

**Files:**
- Modify: `backend/applications/interface/mmseg_segmentation.py`
- Modify: `backend/benchmark_inference_pipeline.py`
- Modify: `backend/test_inference_runner.py`
- Modify: `docs/superpowers/progress/2026-07-15-nvidia-inference-remediation-progress.md`

- [ ] **Step 1: Add precision-context tests**

Assert FP32 uses a null context; FP16 uses `torch.autocast(device_type="cuda", dtype=torch.float16)` only on CUDA; requesting FP16 on CPU fails with `UNSUPPORTED_INFERENCE_PRECISION`.

- [ ] **Step 2: Implement explicit precision context**

Keep default FP32. Do not mutate model weights with `.half()`. Wrap only the forward call, and include effective precision in job result/capability data.

- [ ] **Step 3: Run separate FP32 and FP16 benchmarks**

Use identical inputs, warmup, repeat count and pipeline mode. Compare masks, per-class area ratios, latency, peak GPU memory and failures.

- [ ] **Step 4: Apply the acceptance gate**

FP16 may be documented as an opt-in only if pixel agreement is at least 99.99%, each class area difference is at most 0.02 percentage points, no MMCV/model errors occur, and median total time improves. Otherwise leave FP32 as the only documented production mode.

- [ ] **Step 5: Record checkpoint**

Record measured results and `perf: add validated optional fp16 inference` only if the gate passes; if it fails, retain only benchmark evidence and the explicit rejection result.

### Task 10: End-to-end performance acceptance and release

**Files:**
- Modify: `docs/inference_gpu_compatibility.md`
- Modify: `docs/system_guide.md`
- Modify: `docs/offline_deployment_guide.md`
- Modify: `docs/superpowers/progress/2026-07-15-nvidia-inference-remediation-progress.md`

- [ ] **Step 1: Compare serial and overlap modes**

Run at least one warmup and three measured repeats for each mode on the same accepted lightweight image and target dataset. Record median, p95, stage medians, throughput, memory and disk high-water marks.

- [ ] **Step 2: Decide each optimization separately**

Retain preprocessing concurrency, post-processing concurrency, batch=2 and FP16 only when each independently improves total time without violating correctness. Revert any non-improving option rather than keeping speculative complexity.

- [ ] **Step 3: Run full regression**

Run container backend tests, Miner tests, Node syntax, both builds, Compose assertions, GPU/CPU self-checks, Worker model forward, cancellation, timeout, OOM fallback, restart recovery and output path tests.

- [ ] **Step 4: Set production defaults**

Set `INFERENCE_PIPELINE_MODE=overlap` only if it is not slower than serial and correctness is exact. Keep measured worker/queue counts, batch and precision values explicit in Compose and docs.

- [ ] **Step 5: Update documentation**

Document the measured speedup rather than an estimate, stage bottlenecks, memory limits, debugging commands, serial rollback and unsupported options.

- [ ] **Step 6: Preserve rollback and record completion**

Keep the serial mode and legacy image for one release cycle. Record `release: accept single-task inference pipeline`; commit if Git is available.
