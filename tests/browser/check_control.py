"""Optional real browser check for Phase 8. Use --lan only on a trusted test network."""

from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from playwright.sync_api import Browser, Page, expect, sync_playwright

from poptrap.app import PopTrapApp
from poptrap.core.config import AppConfig, load_config
from poptrap.utils.paths import ENV_PRESETS_DIR


def section(page: Page, key: str) -> None:
    page.locator(f'nav [data-view="{key}"]').click()
    expect(page.locator(f"#view-{key}")).to_be_visible()


def handoff(page: Page, title: str, link: str) -> Page:
    expect(page.locator("#handoff-title")).to_have_text(title)
    with page.expect_popup() as popup:
        page.locator("#handoff").get_by_role("link", name=link, exact=True).click()
    new_page = popup.value
    new_page.wait_for_load_state("domcontentloaded")
    return new_page


def assert_no_playback(page: Page) -> None:
    expect(page.locator("#view-stage")).to_be_hidden(timeout=7000)
    assert page.evaluate("document.querySelectorAll('.popup-card, .poptrap-popup').length === 0")


def creator_and_local(page: Page, app: PopTrapApp) -> None:
    access_url = app.issue_control_url()
    clean_url = access_url.split("#")[0]
    page.goto(clean_url)
    expect(page.locator("#workspace")).to_be_hidden()
    expect(page.locator("#access-message")).to_contain_text("Ctrl+D")
    assert page.request.get(clean_url + "/v1/state").status == 401
    # Browsers may perform a same-document navigation when only a fresh fragment changes.
    page.goto(access_url)
    expect(page.locator("#sync")).to_have_text("Live · local")
    assert page.url == clean_url
    assert page.evaluate("[document.cookie, localStorage.length, sessionStorage.length]") == ["", 0, 0]
    assert page.locator("nav button").count() == 9
    expect(page.locator("#count-active")).to_have_text("0")
    page.locator('nav [data-view="templates"]').press("Enter")
    expect(page.locator("#view-heading")).to_be_focused()
    template = page.locator("#templates li").filter(has=page.get_by_role("heading", name="Countdown Panic", exact=True))
    template.get_by_role("button", name="Prepare session").click()
    ready = handoff(page, "Session prepared", "Open consent page")
    sid = urlsplit(ready.url).path.split("/")[-1]
    expect(ready.locator("#btn-start")).to_be_visible()
    assert_no_playback(ready)
    expect(ready.locator("#sim-badge")).to_have_text("SIMULATION")
    expect(ready.locator("#btn-exit")).to_be_visible()
    section(page, "sessions")
    page.locator("#session-search").fill("not-the-session")
    expect(page.locator("#sessions")).to_contain_text("No sessions match")
    page.locator("#session-search").fill(sid)
    page.locator("#session-status").select_option("ready")
    expect(page.locator("#sessions")).to_contain_text("Not granted")
    page.locator("#sessions summary").press("Enter")
    expect(page.locator("#sessions details")).to_have_attribute("open", "")
    # Consent originates in the existing local recipient page, never the dashboard.
    ready.locator("#btn-start").click()
    expect(ready.locator("#view-stage")).to_be_visible()
    page.locator("#session-status").select_option("active")
    expect(page.locator("#sessions")).to_contain_text("Launch consent recorded")
    ready.keyboard.press("Escape")
    assert_no_playback(ready)
    ready.close()
    page.locator("#session-status").select_option("stopped")
    expect(page.locator("#sessions")).to_contain_text("Stopped")
    page.locator("#cleanup").click()
    expect(page.locator("#feedback")).to_contain_text("Retention windows preserved")
    expect(page.locator("#sessions")).to_contain_text(sid)
    print("Creator: one-time fragment scrubbed, nine views, keyboard navigation, session ID/status filters, real local Launch and Exit synchronized", flush=True)


def presets(page: Page, app: PopTrapApp) -> None:
    section(page, "presets")
    page.locator("#new-experience").click()
    editor = handoff(page, "Studio ready", "Open editor")
    expect(editor.locator("#field-name")).not_to_have_value("")
    editor.locator("#field-name").fill("Control Browser Test")
    editor.locator("#field-title").fill("Test preset title")
    editor.locator("#btn-header-save").click()
    expect(editor.locator("#studio-banner-text")).to_contain_text("Saved local preset")
    editor.close()
    original = page.locator("#presets li").filter(has=page.get_by_role("heading", name="Control Browser Test", exact=True))
    expect(original).to_be_visible(timeout=13_000)
    original.get_by_role("button", name="Edit in Studio").click()
    editor = handoff(page, "Studio ready", "Open editor")
    expect(editor.locator("#field-name")).to_have_value("Control Browser Test")
    expect(editor.locator("#field-title")).to_have_value("Test preset title")
    editor.locator("#field-title").fill("Title updated in Studio")
    editor.locator("#btn-header-save").click()
    editor.get_by_role("button", name="Confirm Overwrite", exact=True).click()
    expect(editor.locator("#studio-banner-text")).to_contain_text("Saved local preset")
    editor.close()
    original.get_by_role("button", name="Prepare preview").click()
    preview = handoff(page, "Preview prepared", "Open preview")
    expect(preview.locator("#btn-start")).to_be_visible()
    assert_no_playback(preview)
    preview.close()
    original.get_by_role("button", name="Prepare session").click()
    prepared = handoff(page, "Session prepared", "Open consent page")
    expect(prepared.locator("#btn-start")).to_be_visible()
    assert_no_playback(prepared)
    assert not app.prank_controller.get_session(prepared.url.rsplit("/", 1)[-1]).consent_granted
    prepared.close()
    original.get_by_role("button", name="Duplicate").click()
    copy = page.locator("#presets li").filter(has=page.get_by_role("heading", name="Control Browser Test (Copy)", exact=True))
    expect(copy).to_be_visible()
    copy.get_by_role("button", name="Delete").click()
    expect(page.locator("#confirm-message")).to_contain_text("Control Browser Test (Copy)")
    expect(page.locator("#confirm-cancel")).to_be_focused()
    page.keyboard.press("Escape")
    expect(copy).to_be_visible()
    copy.get_by_role("button", name="Delete").click()
    page.locator("#confirm-go").click()
    expect(copy).to_have_count(0)
    assert [p.name for p in app.prank_controller.presets.list_presets()[0]] == ["Control Browser Test"]
    print("Presets: created and edited in real Studio, library refreshed, consent-gated preview and session, duplicated, cancelled and confirmed deletion", flush=True)


def share(page: Page, browser: Browser) -> None:
    section(page, "delivery")
    page.locator("#start-loopback").click()
    expect(page.locator("#listener-status")).to_contain_text("Loopback only")
    page.locator("#share-source").select_option("template:countdown")
    page.locator("#create-share").click()
    expect(page.locator("#handoff-title")).to_have_text("Invitation link & QR")
    url = page.locator("#handoff input").input_value()
    qr = page.locator("#handoff img").get_attribute("src")
    assert qr and qr.startswith("data:image/png;base64,")
    with browser.new_context(viewport={"width": 360, "height": 800}, has_touch=True) as context:
        recipient = context.new_page()
        scanner = page.locator("#scan-link").get_attribute("href")
        assert scanner is not None
        recipient.goto(scanner)
        recipient.locator("#scan-file").set_input_files({"name": "invitation.png", "mimeType": "image/png", "buffer": base64.b64decode(qr.split(",", 1)[1])})
        expect(recipient.locator("#scan-url")).to_have_value(url)
        expect(recipient.locator("#delivery-accept")).to_have_count(0)
        recipient.locator("#scan-review").tap()
        expect(recipient.locator("#delivery-accept")).to_be_visible()
        recipient.locator("#delivery-accept").tap()
        expect(recipient.locator("#btn-start")).to_be_visible()
        assert_no_playback(recipient)
        invitation = page.locator("#invitations li").filter(has=page.get_by_role("heading", name="Countdown Panic", exact=True))
        expect(invitation).to_contain_text("Accepted")
        expect(page.locator("#handoff")).to_be_hidden()
        recipient.locator("#btn-start").tap()
        expect(recipient.locator("#view-stage")).to_be_visible()
        expect(recipient.locator("#sim-badge")).to_have_text("SIMULATION")
        expect(recipient.locator("#btn-exit")).to_be_visible()
        invitation.get_by_role("button", name="Revoke invitation").click()
        page.locator("#confirm-go").click()
        expect(invitation).to_contain_text("Revoked")
        assert_no_playback(recipient)
        expect(recipient.locator("#sim-badge")).to_have_text("SIMULATION")
    page.locator("#create-share").click()
    expect(page.locator("#handoff-title")).to_have_text("Invitation link & QR")
    page.locator("#invitations").get_by_role("button", name="Cancel pending", exact=True).click()
    page.locator("#confirm-go").click()
    expect(page.locator("#invitations").get_by_role("button", name="Cancel pending", exact=True)).to_have_count(0)
    expect(page.locator("#handoff")).to_be_hidden()
    print("Share: real QR decode, separate Accept and Launch, revocation stops playback, pending cancellation", flush=True)


def lan(page: Page, browser: Browser) -> None:
    section(page, "delivery")
    page.locator("#stop-listener").click()
    page.locator("#confirm-go").click()
    expect(page.locator("#listener-status")).to_contain_text("Not running")
    expect(page.locator("#start-lan")).to_be_enabled()
    page.locator("#start-lan").click()
    page.locator("#confirm-go").click()
    expect(page.locator("#listener-status")).to_contain_text("Private LAN")
    section(page, "devices")
    expect(page.locator("#devices")).to_contain_text("No opted-in")
    page.locator("#open-joining").click()
    expect(page.locator("#handoff-title")).to_have_text("Availability join link")
    join = page.locator("#handoff input").input_value()
    origin = join.split("/availability#")[0]
    assert page.request.get(origin + "/control/v1/state").status == 404
    with browser.new_context() as ctx_a, browser.new_context() as ctx_b:
        alpha, beta = ctx_a.new_page(), ctx_b.new_page()
        for recipient, label in ((alpha, "Test Alpha"), (beta, "Test Beta")):
            recipient.goto(join)
            expect(recipient.locator("#delivery-announce")).to_be_visible()
            recipient.locator("#delivery-name").fill(label)
            recipient.locator("#delivery-announce").click()
            expect(recipient.locator("#delivery-withdraw")).to_be_visible()
        expect(page.locator("#device-count")).to_have_text("0 selected · 2 opted in")
        page.locator("#devices").get_by_label("Test Alpha").check()
        expect(page.locator("#device-count")).to_have_text("1 selected · 2 opted in")
        page.locator("#select-all").click()
        expect(page.locator("#device-count")).to_have_text("2 selected · 2 opted in")
        page.locator("#select-none").click()
        expect(page.locator("#device-count")).to_have_text("0 selected · 2 opted in")
        page.locator("#select-all").click()
        expect(page.locator("#device-count")).to_have_text("2 selected · 2 opted in")
        page.locator("#queue-source").select_option("template:countdown")
        page.locator("#queue").click()
        expect(page.locator("#feedback")).to_contain_text("2 queued")
        alpha.bring_to_front()
        expect(alpha.locator("#delivery-accept")).to_be_visible()
        alpha.locator("#delivery-accept").click()
        expect(alpha.locator("#btn-start")).to_be_visible()
        assert_no_playback(alpha)
        beta.bring_to_front()
        expect(beta.locator("#delivery-decline")).to_be_visible()
        beta.locator("#delivery-decline").click()
        expect(page.locator("#device-invitations")).to_contain_text("Accepted")
        expect(page.locator("#device-invitations")).to_contain_text("Declined")
        page.locator("#device-invitations").get_by_role("button", name="Revoke invitation", exact=True).click()
        page.locator("#confirm-go").click()
        alpha.bring_to_front()
        expect(alpha.locator("#btn-start")).to_be_hidden()
        assert_no_playback(alpha)
        expect(page.locator("#device-invitations")).to_contain_text("Revoked")
    print("LAN: two independently opted-in browser contexts on actual private bind, individual and bulk selection, queue, accept/decline, revoke; no physical Wi-Fi test", flush=True)


def diagnostics_settings_and_lock(page: Page, app: PopTrapApp, clean: str) -> None:
    page.set_viewport_size({"width": 320, "height": 568})
    for key in ("overview", "sessions", "devices", "delivery", "templates", "presets", "activity", "diagnostics", "settings"):
        section(page, key)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), key
        assert page.locator(f'nav [data-view="{key}"]').bounding_box()["height"] >= 44
    section(page, "activity")
    for event in ("Session created", "Session launched", "Session stopped", "Invitation generated", "Invitation accepted", "Invitation revoked"):
        expect(page.locator("#activity")).to_contain_text(event)
    section(page, "diagnostics")
    expect(page.locator("#browser-checks")).to_contain_text("Canvas 2D")
    page.locator("#run-checks").click()
    for label in ("Runtime maintenance", "Local experience server", "Recipient / LAN listener", "Configuration", "Bluetooth capability", "Studio preset storage"):
        expect(page.locator("#checks")).to_contain_text(label, timeout=16_000)
    expect(page.locator("#checks")).to_contain_text("Local HTTP health response verified")
    expect(page.locator("#checks")).to_contain_text("reachable from this host only")
    section(page, "settings")
    page.get_by_label("Terminal theme").select_option("ice-dark")
    page.get_by_label("Terminal language").select_option("fr")
    page.get_by_role("button", name="Save preferences", exact=True).click()
    expect(page.locator("#feedback")).to_contain_text("saved")
    assert load_config(app.config_path, strict=True).config.ui.default_language == "fr"
    page.reload()
    expect(page.locator("#workspace")).to_be_hidden()
    assert page.url == clean
    page.goto(app.issue_control_url())
    expect(page.locator("#sync")).to_have_text("Live · local")
    page.locator("#lock").click()
    expect(page.locator("#access-message")).to_contain_text("Dashboard locked")
    expect(page.locator("#workspace")).to_be_hidden()
    print("Control UI: no horizontal overflow across nine views at 320px, 44px navigation targets, real host diagnostics, TOML save, reload and Lock", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", default="chromium", choices=["chromium", "firefox", "webkit"])
    parser.add_argument("--lan", action="store_true", help="explicitly opt into a private-interface bind for the LAN check")
    options = parser.parse_args()
    prior_presets = os.environ.get(ENV_PRESETS_DIR)
    with TemporaryDirectory(prefix="poptrap-creator-test-") as tmp, sync_playwright() as p:
        root = Path(tmp)
        os.environ[ENV_PRESETS_DIR] = str(root / "presets")
        app = PopTrapApp(config=AppConfig(), config_path=root / "config.toml")
        try:
            with getattr(p, options.browser).launch(headless=True, executable_path=os.environ.get("POPTRAP_BROWSER_EXECUTABLE")) as browser:
                print(f"{options.browser} {browser.version} (headless)", flush=True)
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                creator_and_local(page, app)
                clean = page.url
                presets(page, app)
                share(page, browser)
                if options.lan:
                    lan(page, browser)
                diagnostics_settings_and_lock(page, app, clean)
                assert not errors, errors
        finally:
            app._shutdown_runtime()
            if prior_presets is None:
                os.environ.pop(ENV_PRESETS_DIR, None)
            else:
                os.environ[ENV_PRESETS_DIR] = prior_presets


if __name__ == "__main__":
    main()
