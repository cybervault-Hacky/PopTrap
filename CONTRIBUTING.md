# Contributing to POPTRAP

Use a dedicated Python 3.11 virtual environment and Node.js 22 for regression tests. Do not install dependencies into system Python.

```bash
python -m pip install --require-hashes --only-binary=:all: -r requirements/test.lock -r requirements/build.lock
python -m pip check
python -m pytest -q
node --test tests/web/*.mjs
python tools/verify_install.py
```

Install `requirements/test.lock` and `requirements/build.lock`, not `requirements.txt`: the latter holds ranges and drifts from the verified profile. Any change to a lock, to `pyproject.toml` dependencies, or to the installation documentation must keep `tests/test_installation.py` and `python tools/verify_install.py` green — the harness re-checks every lock hash against PyPI, wheel availability for each documented platform (including the Termux `linux_aarch64` failure), and a complete fresh install in clean virtual environments. Never weaken a dependency floor, drop a hash, or remove `--only-binary=:all:` to make an install succeed.

Document the expected behavior and add a regression test with the smallest relevant fixture. Keep simulations explicit, local and consent-gated. Test Exit, expiration and teardown for any change to a runtime. Keep the creator and recipient listeners separate, and do not broaden permissions or accept untrusted interface addresses to make tests pass. Test native features with authorized hardware only and distinguish mocks, browser emulation and physical results in review.

Do not commit environment files, logs, user settings, presets, active URLs, browser profiles or generated builds. See the [release workflow](docs/releasing.md) for package verification and [security policy](SECURITY.md) for private reports. Follow the [code of conduct](CODE_OF_CONDUCT.md).
