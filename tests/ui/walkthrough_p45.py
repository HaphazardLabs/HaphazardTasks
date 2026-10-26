# Phase 4/5 walkthrough: recurrence, cycle rollover, templates, backup export/import.
# Run against a throwaway vault (see walkthrough.py). Usage: python walkthrough_p45.py OUT_DIR
import secrets, sys
from pathlib import Path
from playwright.sync_api import sync_playwright
OUT = Path(sys.argv[1]); URL = "http://127.0.0.1:8796/"
PW = "walk-" + secrets.token_hex(8)
MARKER = "ZEBRA-MARKER-8841"
problems, results = [], []
LAST = {"t": None}
def check(name, ok, extra=""):
    results.append(ok); print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
def clear(pg): pg.evaluate("document.getElementById('toast').hidden = true"); LAST["t"] = None
def toast_text(pg):
    for _ in range(100):
        if pg.locator("#toast").is_visible():
            t = pg.inner_text("#toast")
            if t != LAST["t"]:
                LAST["t"] = t; return t
        pg.wait_for_timeout(50)
    return LAST["t"]
def settle(pg, ms=300): pg.wait_for_timeout(ms)
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/opt/google/chrome/chrome", headless=True)
    ctx = b.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
    pg = ctx.new_page()
    pg.on("console", lambda m: m.type in ("error", "warning") and problems.append(f"console {m.type}: {m.text}"))
    pg.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    pg.goto(URL); pg.wait_for_selector("#setup-1:not([hidden])")
    pg.fill("#setup-pass", PW); pg.fill("#setup-pass2", PW); pg.click("#setup-1-btn")
    pg.wait_for_selector("#setup-2:not([hidden])")
    key = "-".join(pg.locator("#rkey span").all_inner_texts())
    pg.fill("#rkey-check", key[-9:]); pg.click("#setup-2-btn"); pg.wait_for_selector("#screen-app:not([hidden])")
    pg.click("#load-examples"); pg.locator(".card").nth(5).wait_for(); settle(pg)

    # --- recurrence on the examples
    wk = pg.locator(".card", has_text="Weekly status report")
    chip = wk.locator(".chip.recur").inner_text()
    check("weekly example shows its repeat chip", chip.startswith("↻ weekly ·"), chip)
    mo = pg.locator(".card", has_text="Monthly access audit")
    check("monthly example shows its repeat chip", "monthly" in mo.locator(".chip.recur").inner_text())
    due_before = wk.locator(".meta").inner_text()
    wk.locator(".title-btn").click(); pg.wait_for_selector("#focus:not([hidden])"); settle(pg)
    check("past cycles section shown for a recurring task", pg.locator("#focus-cycles-wrap").is_visible()
          and "still running" in pg.inner_text("#focus-cycles"))
    for i in (1, 2, 3):
        chip_btn = pg.locator(f"#focus-list .item[data-p='0'][data-i='{i}'] .st")
        clear(pg); chip_btn.focus(); pg.keyboard.press("3"); t = toast_text(pg)
    check("finishing the last item closes the cycle", t.startswith("Cycle complete"), t)
    settle(pg, 500)
    statuses = pg.locator("#focus-list .st").all_inner_texts()
    check("checklist reset to pending", all("Pending" in s for s in statuses), ",".join(statuses))
    rows = pg.locator("#focus-cycles li").all_inner_texts()
    check("past cycle recorded as complete", len(rows) == 1 and "complete" in rows[0].lower() and "4 of 4" in rows[0], rows[0] if rows else "")
    hist = pg.locator("#focus-history li").first.inner_text()
    check("history notes the cycle", "Cycle completed" in hist, hist)
    pg.screenshot(path=str(OUT / "1-cycle.png"))
    pg.keyboard.press("Escape"); settle(pg)
    check("card moved to the next window", wk.locator(".meta").inner_text() != due_before, wk.locator(".meta").inner_text().replace("\n", " | "))

    # --- templates: save from a task, use from the New task menu
    pg.locator(".card", has_text="Q4 vendor security review").locator(".title-btn").click(); pg.wait_for_selector("#focus:not([hidden])")
    clear(pg); pg.click("#focus-template"); t = toast_text(pg)
    check("save as template from focus panel", "Saved “Q4 vendor security review” as a template" in t, t)
    pg.keyboard.press("Escape"); settle(pg)
    pg.click("#new-btn"); pg.wait_for_selector("#menu:not([hidden])")
    opts = pg.locator("#menu [data-new-tpl]").all_inner_texts()
    check("New task menu lists the template", opts == ["Q4 vendor security review"], ",".join(opts))
    pg.screenshot(path=str(OUT / "2-new-menu.png"))
    pg.locator("#menu [data-new-tpl]").first.click(); pg.wait_for_selector("#editor:not([hidden])")
    check("editor prefilled from template", pg.locator(".ed-phase").count() == 3 and pg.locator(".it-text").count() == 8
          and pg.inner_text("#editor-title").startswith("New task from"))
    pg.fill("#ed-title", f"Vendor review, Acme {MARKER}"); clear(pg); pg.click("#ed-save"); t = toast_text(pg)
    card = pg.locator(".card", has_text="Vendor review, Acme"); card.wait_for()
    check("task created from template starts all pending", card.locator(".seg.s-pending").count() == 8, t)

    # --- templates panel: edit (rename), create, delete
    pg.click("#templates-btn"); pg.wait_for_selector("#templates:not([hidden])"); settle(pg)
    check("templates panel lists 1", pg.locator("#tpl-list li").count() == 1)
    pg.locator("#tpl-list [data-tpl-action='edit']").click(); pg.wait_for_selector("#editor:not([hidden])")
    check("template editor hides task-only fields", pg.locator("#ed-start").is_hidden() and pg.locator("#ed-repeat").is_hidden()
          and pg.inner_text("#ed-title-label") == "Template name")
    pg.fill("#ed-title", "Vendor security review"); clear(pg); pg.click("#ed-save"); toast_text(pg)
    pg.wait_for_selector("#templates:not([hidden])"); settle(pg)
    check("rename returns to templates panel", "Vendor security review" in pg.inner_text("#tpl-list"))
    pg.click("#tpl-create"); pg.wait_for_selector("#editor:not([hidden])")
    pg.fill("#ed-title", "Client onboarding"); pg.locator(".it-text").first.fill("Kickoff call"); pg.keyboard.press("Enter"); pg.keyboard.type("Access request")
    clear(pg); pg.click("#ed-save"); toast_text(pg); pg.wait_for_selector("#templates:not([hidden])"); settle(pg)
    check("create template from scratch", pg.locator("#tpl-list li").count() == 2)
    pg.screenshot(path=str(OUT / "3-templates.png"))
    pg.locator("#tpl-list li", has_text="Client onboarding").locator("[data-tpl-action='delete']").click()
    clear(pg); pg.locator("[data-tpl-action='confirm-delete']").click(); t = toast_text(pg); settle(pg)
    check("delete template with confirm", pg.locator("#tpl-list li").count() == 1, t)
    pg.keyboard.press("Escape"); settle(pg)

    # --- repeat controls in the editor
    pg.locator("body").click(position={"x": 5, "y": 5}); pg.keyboard.press("n"); pg.wait_for_selector("#editor:not([hidden])")
    pg.fill("#ed-title", "Twice-weekly sync"); pg.locator(".it-text").first.fill("Agenda")
    pg.select_option("#ed-repeat", "weekly")
    for cb in pg.locator("#ed-rep-weekly input").all(): cb.set_checked(cb.get_attribute("value") in ("0", "3"))
    pg.screenshot(path=str(OUT / "4-repeat.png"))
    clear(pg); pg.click("#ed-save"); toast_text(pg)
    sync = pg.locator(".card", has_text="Twice-weekly sync"); sync.wait_for()
    check("weekly Mon/Thu saved", sync.locator(".chip.recur").inner_text() == "↻ weekly · Mon, Thu")
    sync.locator(".title-btn").click(); pg.wait_for_selector("#focus:not([hidden])"); pg.click("#focus-edit"); pg.wait_for_selector("#editor:not([hidden])")
    check("editor restores weekly days", [cb.is_checked() for cb in pg.locator("#ed-rep-weekly input").all()] == [True, False, False, True, False, False, False])
    pg.select_option("#ed-repeat", "monthly"); pg.select_option("#ed-month-day", "-1")
    clear(pg); pg.click("#ed-save"); toast_text(pg); settle(pg)
    check("switch to monthly, last day", "monthly · last day" in pg.locator("#focus-meta .chip.recur").inner_text())
    pg.click("#focus-edit"); pg.wait_for_selector("#editor:not([hidden])")
    pg.select_option("#ed-repeat", "interval"); pg.fill("#ed-every", "10"); clear(pg); pg.click("#ed-save"); toast_text(pg); settle(pg)
    check("switch to every 10 days", "every 10 days" in pg.locator("#focus-meta .chip.recur").inner_text())
    pg.click("#focus-edit"); pg.wait_for_selector("#editor:not([hidden])")
    pg.select_option("#ed-repeat", "weekly")
    for cb in pg.locator("#ed-rep-weekly input").all(): cb.set_checked(False)
    pg.click("#ed-save"); settle(pg)
    check("weekly with no days is refused", "weekday" in pg.inner_text("#ed-err"))
    pg.select_option("#ed-repeat", ""); clear(pg); pg.click("#ed-save"); toast_text(pg); settle(pg)
    check("turn repeat off", pg.locator("#focus-meta .chip.recur").count() == 0)
    pg.keyboard.press("Escape"); settle(pg)

    # --- backup export / import
    pg.click("#settings-btn"); pg.wait_for_selector("#settings:not([hidden])"); settle(pg)
    with pg.expect_download() as dl:
        pg.click("#backup-export")
    d = dl.value; path = OUT / d.suggested_filename; d.save_as(str(path))
    data = path.read_bytes()
    check("export downloads the encrypted vault", d.suggested_filename.startswith("HaphazardTasks-backup-")
          and data[:8] == b"HTVAULT1" and MARKER.encode() not in data, f"{d.suggested_filename}, {len(data)} bytes")
    pg.keyboard.press("Escape"); settle(pg)
    pg.locator("body").click(position={"x": 5, "y": 5}); pg.keyboard.press("n"); pg.wait_for_selector("#editor:not([hidden])")
    pg.fill("#ed-title", "Made after the backup"); pg.locator(".it-text").first.fill("x"); clear(pg); pg.click("#ed-save"); toast_text(pg)
    pg.click("#settings-btn"); pg.wait_for_selector("#settings:not([hidden])"); settle(pg)
    pg.set_input_files("#import-file", str(path)); pg.fill("#import-pass", "wrong passphrase here")
    pg.click("#import-start"); pg.locator("#import-confirm button[type=submit]").click(); settle(pg, 800)
    check("import with wrong passphrase refused", "does not open" in pg.inner_text("#import-err"))
    pg.set_input_files("#import-file", str(path)); pg.fill("#import-pass", PW)
    pg.click("#import-start"); pg.screenshot(path=str(OUT / "5-import-confirm.png"))
    clear(pg); pg.locator("#import-confirm button[type=submit]").click(); t = toast_text(pg); settle(pg, 600)
    check("import restores the backup", t.startswith("Backup restored") and pg.locator(".card", has_text="Made after the backup").count() == 0
          and pg.locator(".card", has_text="Vendor review, Acme").count() == 1, t)
    pg.click("#settings-btn"); pg.wait_for_selector("#settings:not([hidden])"); settle(pg)
    facts = pg.inner_text("#vault-info")
    check("vault facts after import", "Saved versions" in facts, facts.split("Saved versions")[1].split("\n")[1] if "Saved versions" in facts else "")
    pg.keyboard.press("Escape")
    b.close()
print(f"\n{sum(results)}/{len(results)} checks passed")
print("problems:", problems or "none")
