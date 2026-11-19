"""Optional local Chromium/engine benchmark; no physical-device or power claims.

    python -m tests.browser.measure_performance [--reduced-motion] [--iterations 3]
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import statistics
import time
import tracemalloc

from playwright.sync_api import sync_playwright

from poptrap.engine.controller import PrankController
from poptrap.engine.delivery.server import DeliveryServer
from poptrap.engine.models import MAX_ACTIVE_SESSIONS, TemplateId
from tests.browser.check_recipient import config, no_playback, review_and_launch

RAF_PROBE = """
window.rafRequests = 0;
const nativeRAF = requestAnimationFrame;
window.requestAnimationFrame = (fn) => { rafRequests++; return nativeRAF(fn); };
"""


def engine_cycles() -> dict:
    tick = [0.0]
    controller = PrankController(auto_schedule=False, clock=lambda: tick[0], retention_seconds=1)
    sample = config()
    batches = 128
    gc.collect()
    tracemalloc.start()
    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    try:
        for _ in range(batches):
            sids = [controller.create_session(sample).session_id for _ in range(MAX_ACTIVE_SESSIONS)]
            for sid in sids:
                controller.start_session(sid)
                controller.activate_session(sid)
                controller.build_browser_payload(sid)
            for sid in sids:
                controller.end_session(sid, reason="Benchmark ended.")
            tick[0] += 1
            assert len(controller.run_maintenance().purged_ids) == MAX_ACTIVE_SESSIONS
            assert controller.stored_session_count == 0
        return {
            "cycles": batches * MAX_ACTIVE_SESSIONS,
            "wall_ms_with_tracing": round((time.perf_counter() - wall_start) * 1000),
            "python_cpu_ms_with_tracing": round((time.process_time() - cpu_start) * 1000),
            "python_peak_traced_kib": round(tracemalloc.get_traced_memory()[1] / 1024),
            "stored_sessions_after_cleanup": controller.stored_session_count,
        }
    finally:
        tracemalloc.stop()
        controller.shutdown()


def sample_player(browser, service, template, reduced_motion) -> dict:
    manager = service.manager
    assert manager is not None
    parameters = {"duration_seconds": 10, "sound_enabled": False}
    if template is TemplateId.POPUP_STORM:
        parameters["popup_count"] = 24
    invite = manager.create_share(config(template, **parameters))
    with browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True,
                             device_scale_factor=2, reduced_motion="reduce" if reduced_motion else "no-preference") as ctx:
        ctx.add_init_script(RAF_PROBE)
        page = ctx.new_page()
        metrics = ctx.new_cdp_session(page)
        metrics.send("Performance.enable")
        page.goto(invite.share_url)
        review_and_launch(page, touch=True)

        def read_metrics():
            return {item["name"]: item["value"] for item in metrics.send("Performance.getMetrics")["metrics"]}

        before = read_metrics()
        start = page.evaluate("rafRequests")
        page.wait_for_timeout(1100)
        after = read_metrics()
        frames = page.evaluate("rafRequests") - start
        assert frames <= (0 if reduced_motion else 23), (template, frames)
        stage_nodes = page.locator("#stage-root *").count()
        page.locator("#btn-exit").tap()
        no_playback(page)
        page.wait_for_timeout(150)
        stopped = page.evaluate("rafRequests")
        page.wait_for_timeout(500)
        assert page.evaluate("rafRequests") == stopped
        return {
            "raf_per_1_1s": frames,
            "renderer_task_ms_per_1_1s": round(1000 * (after["TaskDuration"] - before["TaskDuration"]), 1),
            "page_js_heap_kib": round(after["JSHeapUsedSize"] / 1024),
            "stage_descendants_during_playback": stage_nodes,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, choices=range(1, 5), default=3)
    parser.add_argument("--reduced-motion", action="store_true")
    args = parser.parse_args()
    out = {"engine": engine_cycles(), "reduced_motion": args.reduced_motion, "samples_per_template": args.iterations}
    service = DeliveryServer()
    try:
        service.start()
        with sync_playwright() as p, p.chromium.launch(
            executable_path=os.environ.get("POPTRAP_BROWSER_EXECUTABLE"), headless=True,
        ) as browser:
            out["chromium"] = browser.version
            out["viewport"] = "390x844, touch, DPR2"
            out["templates"] = {}
            for template in TemplateId:
                rows = [sample_player(browser, service, template, args.reduced_motion) for _ in range(args.iterations)]
                out["templates"][template.value] = {key: statistics.median(row[key] for row in rows) for key in rows[0]}
    finally:
        service.stop()
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
