# Compatibility matrix: 0.2.0-rc.1

The verified release profile is **Debian 12 Linux x86_64, CPython 3.11.2, Node 22.22.3 (tests only), and headless Chromium 153.0.8010.0**. Status below describes the evidence for this candidate, not every version that can resolve the dependency metadata.

| Area | Evidence | Release status |
| --- | --- | --- |
| Linux x86_64 package and application | Fresh locked install outside the checkout; actual CLI/PTY plus Textual automation; real local listeners and package removal. | **Verified reference platform** for the scoped local workflows below. |
| Terminal layout on Linux | Automated Textual render/navigation, including compact 44 × 12, and a real PTY startup/exit. Capture uses Textual's export. | **Automated**, not broader terminal/desktop certification. |
| Chromium on Linux | Creator and recipient workflows on real loopback and same-host private IPv4; Node browser-component tests. | **Automated real browser**. Not a physical phone or Wi-Fi link. |
| Android/iOS recipient layout | Chromium touch, compact, landscape, focus and interruption automation. | **Emulated**; native mobile permission and browser chrome unverified. No native recipient app required. |
| QR and camera | Real QR decoding from a local image and a synthetic `MediaStream`; track cleanup tested. LAN camera disabled by design. | **Automated/synthetic**, no physical camera or OS permission UI tested. |
| LAN invitations | Separate opted-in browser contexts using an assigned private IPv4 listener on the same Linux host. | **Automated**, no physical router, Wi-Fi or peer-device test. |
| Native Linux Bluetooth | Actual host capability check without Bluetooth hardware; discovery, pairing and transport exercised with mocks. | **Availability check only** on this host. No physical radio test or recipient endpoint certification. |
| Windows x86_64 / macOS Intel and Apple Silicon | CPython 3.11 wheel listings for locked runtime dependencies, re-verified by hash-checked `pip download` for `win_amd64`, `macosx_11_0_arm64` and `macosx_10_12_x86_64`. Platform behavior covered by simulated fixtures only. | **Install instructions provided; native integration unverified.** |
| Linux ARM64 (aarch64) | Hash-checked `pip download --platform manylinux2014_aarch64` resolved all 26 locked pins. No ARM64 execution. | **Install level only.** Not a verified runtime platform. |
| CPython 3.12 / 3.13 / 3.14 | Hash-checked `pip download` for `cp312`, `cp313` and `cp314` resolved all 26 locked pins. The suite and application ran on 3.11 only. | **Install level only.** 3.11 is the supported interpreter. |
| Firefox and WebKit/Safari | Optional runners exist. Previous downloads failed; no successful engine run is part of this candidate. | **Unverified**. WebKit automation would not replace a physical Safari/iOS test. |
| Termux / Android | Platform recognition and degraded Bluetooth states covered by mocks. Hash-checked `pip download --platform linux_aarch64` **fails**: `pydantic-core==2.50.4` publishes no wheel for that tag, and its sdist requires maturin plus Rust 1.88 (`edition 2024`). `tools/verify_install.py` asserts this failure on every run. | **Termux is not a supported install target for this RC.** No Android execution claim. An Android device can participate as a browser recipient of a Linux creator host. |
| Public hosting / Internet delivery | Architecture deliberately restricts exposure. | **Unsupported**. No tunnels, port forwarding, public HTTPS, native remote control, or unattended launch. |

**Physical test evidence:** none for mobile, camera, Wi-Fi, or Bluetooth. A browser/device listing or a successful dependency resolution is not a hardware compatibility claim. See the [release report](phase-10.md) for further release gates.
