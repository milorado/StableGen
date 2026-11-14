# Regression checks

These tests cover the nine findings from the November 7 StableGen review. They
use synthetic images, disposable files and mocked Blender/backend/process calls.
No running ComfyUI, models or credentials are required.

Use Python 3.11+ with NumPy and Pillow 13.0.0 installed, then run from the checkout:

```sh
python -m unittest discover -s tests -v
```

The live process-identity check skips if the OS denies process introspection;
the mocked reused-PID and recovery checks still run and require fail-closed
ownership. Run `tests/blender_regressions.py` in a separate factory-startup Blender
for real geometry invalidation, registration and native GLB export checks.

Before packaging, download and verify every declared platform wheel:

```sh
python scripts/prepare_wheels.py
blender --background --factory-startup --offline-mode --command extension validate stablegen
blender --background --factory-startup --offline-mode --command extension build --source-dir stablegen --output-dir dist
```

The wheel downloader checks exact filenames and PyPI SHA-256 digests and records
them in `stablegen/wheels/verified-wheels.json`. Wheels remain ignored by Git.
Release archives must be rebuilt to ship the patched Pillow dependency.
