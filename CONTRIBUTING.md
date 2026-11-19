# Contributing to POPTRAP

Use a dedicated Python 3.11 virtual environment and Node.js 22 for regression tests. Do not install dependencies into system Python.

```bash
python -m pip install --require-hashes --only-binary=:all: -r requirements/test.lock -r requirements/build.lock
python -m pip check
python -m pytest -q
node --test tests/web/*.mjs
```

Document the expected behavior and add a regression test with the smallest relevant fixture. Keep simulations explicit, local and consent-gated. Test Exit, expiration and teardown for any change to a runtime. Keep the creator and recipient listeners separate, and do not broaden permissions or accept untrusted interface addresses to make tests pass. Test native features with authorized hardware only and distinguish mocks, browser emulation and physical results in review.

Do not commit environment files, logs, user settings, presets, active URLs, browser profiles or generated builds. See the [release workflow](docs/releasing.md) for package verification and [security policy](SECURITY.md) for private reports. Follow the [code of conduct](CODE_OF_CONDUCT.md).
