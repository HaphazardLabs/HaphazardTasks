# Headless-Chrome walkthrough of every view by mouse and keyboard (needs: pip install playwright).
# Run against a throwaway vault:  HAPHAZARDTASKS_DATA=/tmp/ht-ui .venv/bin/python -m app.run
# then:  python tests/ui/walkthrough.py /tmp/ht-shots   (takes ~90 s: includes a real 1-minute auto-lock)
import secrets, sys, time
from playwright.sync_api import sync_playwright
OUT = sys.argv[1]; URL = "http://127.0.0.1:8796/"
PW = "walk-" + secrets.token_hex(8)
problems, results = [], []
def check(name, ok, extra=""):
    results.append(ok); print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))
LAST = {"t": None}
def toast_text(pg):
    """Wait for a toast whose text differs from the last one read (toasts linger for 3 s)."""
    for _ in range(100):
        if pg.locator("#toast").is_visible():
            t = pg.inner_text("#toast")
            if t != LAST["t"]:
                LAST["t"] = t; return t
        pg.wait_for_timeout(50)
    return LAST["t"]
def settle(pg, ms=250): pg.wait_for_timeout(ms)
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/opt/google/chrome/chrome", headless=True)
    pg = b.new_page(viewport={"width": 1440, "height": 1000})
    pg.on("console", lambda m: m.type in ("error", "warning") and problems.append(f"console {m.type}: {m.text}"))
    pg.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    pg.goto(URL); pg.wait_for_selector("#setup-1:not([hidden])")
    pg.fill("#setup-pass", PW); pg.fill("#setup-pass2", PW); pg.click("#setup-1-btn")
    pg.wait_for_selector("#setup-2:not([hidden])")
    key = "-".join(pg.locator("#rkey span").all_inner_texts())
    pg.fill("#rkey-check", key[-9:]); pg.click("#setup-2-btn"); pg.wait_for_selector("#screen-app:not([hidden])")
    pg.click("#load-examples"); pg.locator(".card").nth(5).wait_for(); settle(pg, 400)

    # Overview
    ov = pg.locator("#overview")
    check("overview visible with one ring per task", ov.is_visible() and pg.locator("#ov-ring .track").count() == 6)
    badges = pg.locator("#ov-key .health").all_inner_texts()
    check("health badges", "OVERDUE" in [x.upper() for x in badges] and "BEHIND" in [x.upper() for x in badges], ", ".join(badges))
    check("stats tiles", pg.locator("#stats .stat").count() == 4, " | ".join(pg.locator("#stats .stat").all_inner_texts()).replace("\n", " "))
    pg.screenshot(path=f"{OUT}/1-overview.png")
    pg.locator("#ov-key button", has_text="Contract renewal").click(); pg.wait_for_selector("#focus:not([hidden])")
    check("overview row opens focus panel (mouse)", pg.inner_text("#focus-title") == "Contract renewal, Site B")
    pg.keyboard.press("Escape"); settle(pg)
    check("Esc closes focus panel", pg.locator("#focus").is_hidden())

    # Search and tags (keyboard)
    pg.locator("body").click(position={"x": 5, "y": 5}); pg.keyboard.press("/")
    check("/ focuses search", pg.evaluate("document.activeElement.id") == "search")
    pg.keyboard.type("pen-test"); settle(pg)
    check("search filters by item text", pg.locator(".card").count() == 1 and "Q4 vendor" in pg.locator(".card .title-btn").inner_text())
    pg.keyboard.press("Escape"); settle(pg)
    check("Esc clears search", pg.locator(".card").count() == 6 and pg.input_value("#search") == "")
    pg.select_option("#tag-sel", "example"); settle(pg)
    check("tag filter (select)", pg.locator(".card").count() == 6)
    pg.select_option("#tag-sel", ""); settle(pg)

    # New task by keyboard only
    pg.locator("body").click(position={"x": 5, "y": 5}); pg.keyboard.press("n"); pg.wait_for_selector("#editor:not([hidden])")
    check("N opens the editor", pg.evaluate("document.activeElement.id") == "ed-title")
    pg.keyboard.type("Keyboard-made task")
    pg.focus("#ed-tags"); pg.keyboard.type("client-a, q4")
    pg.locator(".it-text").first.focus(); pg.keyboard.type("Alpha"); pg.keyboard.press("Enter"); pg.keyboard.type("Bravo")
    pg.keyboard.press("Enter"); pg.keyboard.type("Charlie")
    pg.keyboard.press("Alt+ArrowUp"); settle(pg, 100)
    order = [pg.locator(".it-text").nth(i).input_value() for i in range(3)]
    check("Alt+Up moves an item", order == ["Alpha", "Charlie", "Bravo"], ",".join(order))
    pg.click("#ed-add-phase"); pg.keyboard.type("Second phase")
    pg.locator(".ed-phase").nth(1).locator(".it-text").fill("Delta")
    # drag Alpha from phase 1 into phase 2 with the mouse
    pg.locator(".ed-phase").nth(0).locator(".ed-item").nth(0).locator(".drag").drag_to(pg.locator(".ed-phase").nth(1).locator(".ed-item").nth(0))
    settle(pg, 150)
    p1 = [x.input_value() for x in pg.locator(".ed-phase").nth(0).locator(".it-text").all()]
    p2 = [x.input_value() for x in pg.locator(".ed-phase").nth(1).locator(".it-text").all()]
    check("drag moves an item between phases", "Alpha" in p2 and "Alpha" not in p1, f"{p1} / {p2}")
    pg.locator(".ed-phase").nth(1).locator(".ph-name").focus(); pg.keyboard.press("Alt+ArrowUp"); settle(pg, 100)
    check("Alt+Up moves a phase", pg.locator(".ph-name").nth(0).input_value() == "Second phase")
    pg.screenshot(path=f"{OUT}/2-editor.png")
    pg.evaluate("document.getElementById('toast').hidden = true"); LAST["t"] = None
    pg.focus("#ed-title"); pg.keyboard.press("Enter")
    check("Enter in title saves", "Task created" in toast_text(pg))
    card = pg.locator(".card", has_text="Keyboard-made task"); card.wait_for()
    check("tags saved and shown", card.locator(".chip.tag").all_inner_texts() == ["client-a", "q4"])
    check("new task has 2 phase rings", card.locator("svg .track").count() == 2)
    card.locator(".chip.tag", has_text="client-a").click(); settle(pg)
    check("tag chip filters the grid", pg.locator(".card").count() == 1)
    pg.select_option("#tag-sel", ""); settle(pg)

    # Grid checklist keyboard: arrow + number keys
    q4 = pg.locator(".card", has_text="Q4 vendor security review")
    q4.locator(".item[data-p='0'][data-i='0'] .st").focus()
    for _ in range(4): pg.keyboard.press("ArrowDown")
    focused = pg.evaluate("document.activeElement.closest('.item').dataset.p + '/' + document.activeElement.closest('.item').dataset.i")
    check("ArrowDown walks the checklist across phases", focused == "1/1", focused)
    pg.evaluate("document.getElementById('toast').hidden = true"); LAST["t"] = None; pg.keyboard.press("3"); t = toast_text(pg)
    check("number key 3 sets Done", "Interview vendor point of contact → Done" in t, t)
    check("focus stays on the item after a change", pg.evaluate("document.activeElement.closest('.item').dataset.i") == "1")

    # Focus panel by keyboard: Enter on title
    q4.locator(".title-btn").focus(); pg.keyboard.press("Enter"); pg.wait_for_selector("#focus:not([hidden])"); settle(pg, 400)
    check("Enter on a title opens focus panel", pg.inner_text("#focus-title") == "Q4 vendor security review")
    check("notes render Markdown safely", pg.locator("#focus-notes strong").count() == 1 and pg.locator("#focus-notes li").count() == 2)
    hist = pg.locator("#focus-history li").all_inner_texts()
    check("history shows the status change", any("Interview vendor point of contact: Underway → Done" in h for h in hist), hist[0] if hist else "")
    pg.locator("#focus-list .item[data-p='2'][data-i='0'] .st").focus(); pg.keyboard.press("2"); settle(pg, 400)
    check("number keys work in the focus panel", "Underway" in pg.locator("#focus-list .item[data-p='2'][data-i='0'] .st").inner_text())
    check("focus panel history refreshes", "Legal review: Pending → Underway" in pg.locator("#focus-history li").first.inner_text())
    pg.locator("#focus-list .item[data-p='2'][data-i='1'] .st").click(); pg.wait_for_selector("#menu:not([hidden])")
    pg.keyboard.press("4"); settle(pg, 400)
    check("status menu inside focus panel (mouse + key 4)", "Blocked" in pg.locator("#focus-list .item[data-p='2'][data-i='1'] .st").inner_text())
    pg.screenshot(path=f"{OUT}/3-focus.png")

    # Edit from focus returns to focus
    pg.click("#focus-edit"); pg.wait_for_selector("#editor:not([hidden])")
    pg.fill("#ed-title", "Q4 vendor security review (edited)"); pg.click("#ed-save"); settle(pg, 400)
    check("save from focus returns to focus panel", pg.locator("#focus").is_visible() and "(edited)" in pg.inner_text("#focus-title"))

    # Archive and restore
    pg.evaluate("document.getElementById('toast').hidden = true"); LAST["t"] = None; pg.click("#focus-archive"); t = toast_text(pg)
    check("archive from focus", "Archived" in t and pg.inner_text("#count-archive") == "1", f"{t} / count={pg.inner_text('#count-archive')}")
    pg.focus("#view-archive"); pg.keyboard.press("Enter"); settle(pg)
    check("Archive view via keyboard", pg.locator(".card.archived").count() == 1 and pg.locator("#overview").is_hidden())
    pg.screenshot(path=f"{OUT}/4-archive.png")
    pg.locator(".card.archived .title-btn").click(); pg.wait_for_selector("#focus:not([hidden])")
    check("archived task is read-only", pg.locator("#focus-edit").is_hidden() and pg.locator("#focus-list button.st").count() == 0
          and pg.inner_text("#focus-archive") == "Restore")
    pg.keyboard.press("Escape"); settle(pg)
    pg.evaluate("document.getElementById('toast').hidden = true"); LAST["t"] = None; pg.locator("[data-restore]").click(); t = toast_text(pg)
    check("restore from archive card", "Restored" in t and pg.inner_text("#count-archive") == "0", f"{t} / count={pg.inner_text('#count-archive')}")
    pg.click("#view-active"); settle(pg)

    # Delete from focus
    pg.locator(".card", has_text="Keyboard-made task").locator(".title-btn").click(); pg.wait_for_selector("#focus:not([hidden])")
    pg.click("#focus-delete"); pg.evaluate("document.getElementById('toast').hidden = true"); LAST["t"] = None; pg.click("#focus-confirm-yes"); t = toast_text(pg)
    check("delete from focus panel", "Deleted" in t and pg.locator(".card", has_text="Keyboard-made task").count() == 0)

    # Sort
    pg.select_option("#sort-sel", "priority"); settle(pg)
    pris = [c.locator(".chip").first.inner_text() for c in pg.locator(".card").all()]
    check("sort by priority", pris == sorted(pris, key=["High", "Medium", "Low"].index), ",".join(pris))

    # Settings: theme + auto-lock
    pg.click("#settings-btn"); pg.wait_for_selector("#settings:not([hidden])"); settle(pg, 300)
    check("settings load current values", pg.input_value("#set-autolock") == "15" and pg.input_value("#set-theme") == "system")
    pg.select_option("#set-theme", "dark"); pg.fill("#set-autolock", "1"); pg.click("#prefs-form button[type=submit]")
    t = toast_text(pg); check("save preferences", "1 minute" in t, t)
    check("dark theme applied", pg.evaluate("document.documentElement.dataset.theme") == "dark")
    pg.keyboard.press("Escape"); settle(pg, 300)
    pg.screenshot(path=f"{OUT}/5-dark.png")
    idle = pg.inner_text("#idle"); check("countdown reflects 1-minute auto-lock", idle.startswith("0:") or idle == "1:00", idle)
    # Real auto-lock: no input for just over a minute
    pg.mouse.move(1, 1); t0 = time.time()
    pg.wait_for_selector("#screen-unlock:not([hidden])", timeout=100000)
    check("auto-lock fired in the browser", True, f"{time.time() - t0:.0f}s")
    check("lock screen keeps dark theme", pg.evaluate("document.documentElement.dataset.theme") == "dark")
    check("dashboard wiped on lock", pg.locator(".card").count() == 0 and pg.locator("#ov-key li").count() == 0)
    pg.fill("#unlock-pass", PW); pg.click("#unlock-btn"); pg.locator(".card").nth(5).wait_for(); settle(pg)
    check("data intact after auto-lock", pg.locator(".card", has_text="(edited)").count() == 1)
    pg.locator("body").click(position={"x": 5, "y": 5}); pg.keyboard.press("l")
    pg.wait_for_selector("#screen-unlock:not([hidden])"); check("L locks", True)

    ph = b.new_context(viewport={"width": 390, "height": 900}).new_page(); ph.goto(URL)
    ph.fill("#unlock-pass", PW); ph.click("#unlock-btn"); ph.locator(".card").nth(5).wait_for(); ph.wait_for_timeout(300)
    check("phone: no horizontal overflow", not ph.evaluate("document.documentElement.scrollWidth > innerWidth"))
    ph.screenshot(path=f"{OUT}/6-phone.png")
    ph.locator(".card .title-btn").first.click(); ph.wait_for_selector("#focus:not([hidden])"); ph.wait_for_timeout(300)
    check("phone: focus panel fits", not ph.evaluate("document.getElementById('focus').scrollWidth > innerWidth"))
    ph.screenshot(path=f"{OUT}/7-phone-focus.png")
    b.close()
print(f"\n{sum(results)}/{len(results)} checks passed")
print("problems:", problems or "none")
