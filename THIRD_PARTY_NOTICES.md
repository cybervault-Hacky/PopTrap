# Third-party notices

POPTRAP's original code is licensed under [MIT](LICENSE). The distribution also
contains **jsQR 1.4.0**, by the contributors to [jsQR](https://github.com/cozmo/jsQR),
under the Apache License, Version 2.0. The decoder and license are copied unchanged
from the [npm release](https://registry.npmjs.org/jsqr/-/jsqr-1.4.0.tgz) into
`poptrap/web/scripts/vendor/`. The [full license text](poptrap/web/scripts/vendor/jsQR.LICENSE.txt)
is included in the wheel and source archive. The browser loads the bundled copy
locally, not from npm or a CDN.

The Python dependencies named in `pyproject.toml` and the lock files are installed
separately. Their code is not bundled into POPTRAP's wheel or source archive;
the respective distributions carry their own terms and notices.
