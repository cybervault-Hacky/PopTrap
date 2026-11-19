"""Optional real-browser suite; emulates viewports, not phones or camera hardware.

Run from the project root: python -m tests.browser.check_recipient [--browser chromium]
"""

from __future__ import annotations

import argparse
import io
import json
import os

from playwright.sync_api import Browser, Page, expect, sync_playwright

from poptrap.engine.controller import PrankController
from poptrap.engine.delivery.security import invitation_qr, list_lan_interfaces
from poptrap.engine.delivery.server import DeliveryServer
from poptrap.engine.models import TemplateId
from poptrap.engine.registry import create_default_registry
from poptrap.engine.runtime.server import ExperienceServer

REGISTRY = create_default_registry()


def config(template=TemplateId.GLITCH, **updates):
    return REGISTRY.get(template).build_default_config().model_copy(update=updates)


def geometry(page: Page, ids: tuple[str, ...]) -> None:
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    for selector in ids:
        button = page.locator(selector)
        assert button.is_visible(), selector
        rect = button.bounding_box()
        assert rect and min(rect["width"], rect["height"]) >= 44, (selector, rect)


def no_playback(page: Page) -> None:
    expect(page.locator("#view-stage")).to_be_hidden()
    assert page.evaluate("PopTrapEffects.getActiveCount() === 0")
    assert page.locator("#stage-root > *").count() == 0


def review_and_launch(page: Page, *, touch: bool) -> None:
    accept = page.locator("#delivery-accept")
    expect(accept).to_be_visible()
    geometry(page, ("#delivery-accept", "#delivery-decline", "#delivery-exit"))
    accept.tap() if touch else accept.click()
    expect(page.locator("#btn-start")).to_be_visible()
    expect(page.locator("#btn-start")).to_be_enabled()
    no_playback(page)
    assert page.evaluate("location.pathname === '/play' && location.hash === ''")
    launch = page.locator("#btn-start")
    launch.tap() if touch else launch.press("Enter")
    expect(page.locator("#view-stage")).to_be_visible()
    expect(page.locator("#sim-badge")).to_have_text("SIMULATION")


def camera_flow(browser: Browser, endpoint, invitation) -> None:
    # The permission call is mocked. The stream, canvas frames, decoder and
    # track shutdown are real browser code; no camera hardware is exercised.
    rows = tuple(tuple(row) for row in invitation_qr(endpoint, invitation.share_url).matrix_iter(border=4))
    with browser.new_context() as ctx:
        ctx.add_init_script("""if (navigator.mediaDevices) {
          window.captureCalls = 0;
          navigator.mediaDevices.getUserMedia = async (opts) => {
            window.captureCalls++;
            if (opts.audio !== false) throw new Error("The QR helper must not request a microphone");
            const matrix = """ + json.dumps(rows) + """;
            const canvas = document.createElement('canvas');
            canvas.width = 640; canvas.height = 480;
            const drawing = canvas.getContext('2d');
            window.testStream = canvas.captureStream(10);
            drawing.fillStyle = '#fff'; drawing.fillRect(0, 0, 640, 480);
            drawing.fillStyle = '#000';
            matrix.forEach((row, y) => row.forEach((dark, x) => {
              if (dark) drawing.fillRect(120 + x * 8, 28 + y * 8, 8, 8);
            }));
            return window.testStream;
          };
        }""")
        page = ctx.new_page()
        page.goto(endpoint.origin + "/scan")
        expect(page.locator("#scan-camera")).to_be_enabled()
        page.locator("#scan-refresh").click()
        assert page.evaluate("captureCalls") == 0
        page.locator("#scan-camera").click()
        expect(page.locator("#scan-url")).to_have_value(invitation.share_url, timeout=12_000)
        assert page.evaluate("captureCalls === 1 && testStream.getTracks().every(t => t.readyState === 'ended')")
        assert page.evaluate("document.getElementById('scan-video').srcObject === null")
        page.locator("#scan-review").click()
        review_and_launch(page, touch=False)
        page.reload()
        expect(page.locator("#status-title")).to_have_text("Delivery session unavailable")
        no_playback(page)
        print("Loopback camera: synthetic MediaStream read real QR, released tracks; reload inert", flush=True)


def recipient_matrix(browser: Browser, service: DeliveryServer, engine: str) -> None:
    manager = service.manager
    assert manager is not None
    endpoint = manager.endpoint
    for i, template in enumerate(TemplateId):
        viewport = ((320, 568), (360, 800), (390, 844), (390, 750), (1440, 900))[i]
        width, height = viewport
        touch = i < 4
        reduced = not touch
        config_args = {"duration_seconds": 10 if template == TemplateId.POPUP_STORM else 6, "sound_enabled": touch}
        if template == TemplateId.POPUP_STORM:
            config_args["popup_count"] = 24
        invite = manager.create_share(config(template, **config_args))
        with browser.new_context(
            viewport={"width": width, "height": height},
            is_mobile=touch and engine != "firefox", has_touch=touch,
            device_scale_factor=2 if touch else 1,
            reduced_motion="reduce" if reduced else "no-preference",
        ) as ctx:
            ctx.add_init_script("""window.rafRequests = 0;
                const nativeRAF = requestAnimationFrame;
                window.requestAnimationFrame = function(fn) { rafRequests++; return nativeRAF(fn); };""")
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda err: errors.append(str(err)))
            if i < 2:
                page.goto(endpoint.origin + "/scan")
                geometry(page, ("#scan-review", "#scan-exit", "#scan-refresh", ".scan-file-control .btn"))
                # Shrink the visible layout like a software keyboard; this is
                # only viewport/focus automation, not a real mobile keyboard.
                page.set_viewport_size({"width": width, "height": 275})
                geometry(page, ("#scan-review", "#scan-exit"))
                page.locator("#scan-url").fill("https://example.invalid/invite#" + "A" * 43)
                page.locator("#scan-url").press("Enter")
                expect(page.locator("#scan-status")).to_contain_text("Other sites")
                assert page.url.endswith("/scan")
                expect(page.locator("#scan-url")).to_be_focused()
                if i == 0:
                    image = io.BytesIO()
                    invitation_qr(endpoint, invite.share_url).save(image, kind="png", scale=7)
                    page.locator("#scan-file").set_input_files({
                        "name": "invite.png", "mimeType": "image/png", "buffer": image.getvalue(),
                    })
                    expect(page.locator("#scan-url")).to_have_value(invite.share_url, timeout=8_000)
                    expect(page.locator("#scan-status")).to_contain_text("Nothing has started")
                    assert page.url.endswith("/scan")
                else:
                    page.locator("#scan-url").fill(invite.share_url)
                page.locator("#scan-url").press("Enter")
                page.set_viewport_size({"width": width, "height": height})
            else:
                page.goto(invite.share_url)
            review_and_launch(page, touch=touch)
            active_controls = ("#btn-exit", "#btn-theme") + (("#btn-mute",) if touch else ())
            geometry(page, active_controls)
            if i in (0, 3):
                page.set_viewport_size({"width": 844, "height": 390})
                geometry(page, active_controls)
            frame_start = page.evaluate("rafRequests")
            page.wait_for_timeout(1100)
            frames = page.evaluate("rafRequests") - frame_start
            assert frames <= (23 if touch else 0), (template, frames)
            assert page.evaluate("!document.fullscreenElement && window.scrollX === 0")
            outcome = "Exit"
            if template == TemplateId.POPUP_STORM:
                expect(page.locator(".storm-arena")).to_have_class("storm-arena is-stacked")
                page.wait_for_timeout(3200)
                assert 4 <= page.locator(".popup-card").count() <= 8
                geometry(page, (".popup-close >> nth=0",))
                page.evaluate("scrollTo(0, document.documentElement.scrollHeight)")
                exit_box = page.locator("#btn-exit").bounding_box()
                assert exit_box and 0 <= exit_box["y"] < 60, exit_box
                page.locator(".popup-close").last.tap()
                page.locator("#btn-exit").tap()
            elif template == TemplateId.SYSTEM_CRASH:
                outcome = "offline"
                ctx.set_offline(True)
                expect(page.locator("#reveal-summary")).to_contain_text("will not resume")
                ctx.set_offline(False)
                page.wait_for_timeout(1500)
            elif template == TemplateId.VIRUS_ALERT:
                outcome = "revocation"
                manager.revoke(invite.id)
                expect(page.locator("#reveal-summary")).to_contain_text("revoked", timeout=4500)
            elif template == TemplateId.COUNTDOWN:
                outcome = "time limit (reduced motion)"
                expect(page.locator("#view-reveal")).to_be_visible(timeout=6600)
            else:
                page.locator("#btn-exit").tap()
            no_playback(page)
            assert not errors, errors
            print(f"{template.value}: {width}×{height}, {frames} RAF requests/1.1s, clean {outcome}", flush=True)


def unavailable_asset_flow(browser: Browser, service: DeliveryServer) -> None:
    manager = service.manager
    assert manager is not None
    invite = manager.create_share(config())
    with browser.new_context() as ctx:
        page = ctx.new_page()
        page.route("**/static/styles/**", lambda route: route.abort())
        page.route("**/static/scripts/effects.js", lambda route: route.abort())
        page.goto(invite.share_url)
        page.locator("#delivery-accept").click()
        expect(page.locator("#status-title")).to_have_text("Simulation unavailable")
        expect(page.locator("#btn-start")).to_be_hidden()
        expect(page.locator("#btn-start")).to_be_disabled()
        expect(page.locator("#view-stage")).to_be_hidden()
        assert manager.get_invitation(invite.id).consent_granted is False
        manager.revoke(invite.id)
        print("Missing CSS or player module: no exposed Launch, consent, or playback", flush=True)


def legacy_flow(browser: Browser) -> None:
    controller = PrankController()
    server = ExperienceServer(controller)
    server.start()
    try:
        with browser.new_context() as ctx:
            page = ctx.new_page()
            session = controller.start_session(controller.create_session(config()).session_id)
            path = server.session_url(session.session_id)
            page.goto(path)
            expect(page.locator("#btn-start")).to_be_visible()
            page.locator("#btn-start").press("Enter")
            expect(page.locator("#view-stage")).to_be_visible()
            second = ctx.new_page()
            second.goto(path)
            # Both event orders (the first view stopped on hide or was still
            # active) must keep the second document from inheriting playback.
            expect(second.locator("#view-reveal:visible").or_(second.get_by_text("Session already started", exact=True))).to_be_visible()
            expect(second.locator("#view-stage")).to_be_hidden()
            expect(second.locator("#btn-start")).to_be_hidden()
            no_playback(second)
            page.bring_to_front()
            page.goto(server.base_url + "/")
            page.go_back()
            expect(page.locator("#view-reveal")).to_be_visible()
            no_playback(page)
            assert page.locator("#btn-start").is_hidden()
            print("Legacy /s: another document and back navigation never inherit playback", flush=True)
    finally:
        server.stop()
        controller.shutdown()


def lan_flow(browser: Browser) -> None:
    interfaces = list_lan_interfaces()
    if not interfaces:
        raise RuntimeError("No eligible private IPv4 interface for the explicitly requested LAN check.")
    service = DeliveryServer()
    try:
        endpoint = service.start(interfaces[0], lan_confirmed=True)
        manager = service.manager
        assert manager is not None
        invitation = manager.create_share(config())
        with browser.new_context(viewport={"width": 320, "height": 568}, has_touch=True) as ctx:
            page = ctx.new_page()
            page.goto(endpoint.origin + "/scan")
            expect(page.locator("#scan-camera")).to_be_disabled()
            expect(page.locator("#scan-camera-hint")).to_contain_text("private LAN HTTP")
            assert page.evaluate("window.isSecureContext") is False
            page.locator("#scan-url").fill(invitation.share_url)
            page.locator("#scan-review").click()
            review_and_launch(page, touch=True)
            page.keyboard.press("Escape")
            no_playback(page)
            print("Private IPv4 listener: no camera; manual link → details → Accept → Launch → Escape", flush=True)
    finally:
        service.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", default="chromium", choices=["chromium", "firefox", "webkit"])
    parser.add_argument("--lan", action="store_true", help="explicitly opt in to binding the first eligible private interface")
    parser.add_argument("--fake-camera", action="store_true", help="test with a mocked permission call and synthetic camera stream")
    options = parser.parse_args()
    if options.fake_camera and options.browser != "chromium":
        parser.error("--fake-camera requires Chromium")
    service = DeliveryServer()
    try:
        endpoint = service.start()
        with sync_playwright() as p, getattr(p, options.browser).launch(
            headless=True, executable_path=os.environ.get("POPTRAP_BROWSER_EXECUTABLE"),
        ) as browser:
            print(f"{options.browser} {browser.version} (headless)", flush=True)
            if options.fake_camera:
                assert service.manager is not None
                camera_flow(browser, endpoint, service.manager.create_share(config()))
            recipient_matrix(browser, service, options.browser)
            # Use a fresh loopback listener for this check so it is unaffected by
            # the short per-peer budgets from the template/QR matrix.
            service.stop()
            service.start()
            unavailable_asset_flow(browser, service)
            legacy_flow(browser)
            if options.lan:
                lan_flow(browser)
    finally:
        service.stop()


if __name__ == "__main__":
    main()
