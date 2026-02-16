import json
import os
import re
import sys
import time
from datetime import datetime

import yaml
from playwright.sync_api import sync_playwright

CONFIG_PATH = os.getenv("CONFIG_PATH", "config.yaml")


def load_config(path: str) -> dict:
    if not os.path.exists(path):
        print(f"Config not found: {path}")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


def load_state(path: str) -> dict:
    if not os.path.exists(path):
        return {"jobs": {}}
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {"jobs": {}}


def save_state(path: str, state: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def find_jobs_page(browser):
    for context in browser.contexts:
        for page in context.pages:
            if "linkedin.com/jobs" in page.url:
                return page
    return None


def prompt_retry(message: str) -> None:
    print(message)
    input("Press Enter to retry...")


def apply_location_filter(page, location: str) -> None:
    selectors = [
        "input[aria-label='City, state, or zip code']",
        "input[aria-label='Location']",
        "input[placeholder*='Location']",
    ]
    input_box = None
    for sel in selectors:
        locator = page.locator(sel)
        if locator.count() > 0:
            input_box = locator.first
            break
    if not input_box:
        raise RuntimeError("Location input not found")
    # Clear existing value
    clear_selectors = [
        "button[aria-label='Clear location']",
        "button[aria-label='Clear']",
        "button[aria-label='Clear search']",
    ]
    cleared = False
    for sel in clear_selectors:
        btn = page.locator(sel)
        if btn.count() > 0 and btn.first.is_visible():
            btn.first.click()
            cleared = True
            break
    input_box.click()
    if not cleared:
        # Try both macOS and Windows/Linux shortcuts
        for combo in ("Meta+A", "Control+A"):
            try:
                input_box.press(combo)
                input_box.press("Backspace")
            except Exception:
                pass

    input_box.fill("")
    input_box.type(location, delay=25)
    time.sleep(0.3)

    # Prefer selecting the exact suggestion when available
    suggestions = page.locator("ul[role='listbox'] li, div[role='listbox'] li")
    if suggestions.count() > 0:
        candidate_texts = [location]
        if location.lower() in ("türkiye", "turkiye"):
            candidate_texts.append("Turkey")
        if location.lower() == "turkey":
            candidate_texts.append("Türkiye")

        for candidate in candidate_texts:
            match = suggestions.filter(has_text=re.compile(re.escape(candidate), re.I))
            if match.count() > 0 and match.first.is_visible():
                match.first.click()
                return

        # As a last resort, click the first suggestion
        if suggestions.first.is_visible():
            suggestions.first.click()
            return

    input_box.press("Enter")


def apply_date_posted_filter(page, label: str) -> None:
    page.get_by_role("button", name=re.compile("Date posted", re.I)).click()
    # try radio buttons first
    option = page.get_by_role("radio", name=re.compile(label, re.I))
    if option.count() == 0:
        option = page.get_by_label(re.compile(label, re.I))
    option.first.click()
    # apply
    apply_btn = page.get_by_role("button", name=re.compile("Show results|Apply", re.I))
    if apply_btn.count() > 0:
        apply_btn.first.click()


def apply_easy_apply_filter(page) -> None:
    # Try direct filter button
    try:
        btn = page.get_by_role("button", name=re.compile("^Easy Apply$", re.I))
        if btn.count() > 0:
            pressed = btn.first.get_attribute("aria-pressed")
            if pressed != "true":
                btn.first.click()
                time.sleep(0.3)
            return
    except Exception:
        pass

    # Try within All filters
    page.get_by_role("button", name=re.compile("All filters", re.I)).click()
    checkbox = page.get_by_role("checkbox", name=re.compile("Easy Apply", re.I))
    if checkbox.count() == 0:
        checkbox = page.locator("label:has-text('Easy Apply') input[type='checkbox']")
    if checkbox.count() > 0:
        checkbox.first.check()
    page.get_by_role("button", name=re.compile("Show results|Apply", re.I)).first.click()


def clear_distance_filter(page) -> None:
    # Prefer the top-level Distance filter if present
    opened = False
    try:
        btn = page.get_by_role("button", name=re.compile("^Distance$", re.I))
        if btn.count() > 0:
            btn.first.click()
            opened = True
    except Exception:
        pass

    if not opened:
        try:
            page.get_by_role("button", name=re.compile("All filters", re.I)).click()
            opened = True
        except Exception:
            return

    option = page.get_by_role("radio", name=re.compile("Any distance|Any", re.I))
    if option.count() == 0:
        option = page.get_by_label(re.compile("Any distance|Any", re.I))
    if option.count() > 0:
        option.first.click()
        apply_btn = page.get_by_role("button", name=re.compile("Show results|Apply", re.I))
        if apply_btn.count() > 0:
            apply_btn.first.click()


def apply_filters(page, filters: dict) -> None:
    failures = []
    if filters.get("location"):
        try:
            apply_location_filter(page, filters["location"])
        except Exception as e:
            failures.append(f"location ({e})")
    # Clear distance when not explicitly set
    if "distance" in filters and not filters.get("distance"):
        try:
            clear_distance_filter(page)
        except Exception as e:
            failures.append(f"distance ({e})")
    if filters.get("time_posted"):
        try:
            apply_date_posted_filter(page, filters["time_posted"])
        except Exception as e:
            failures.append(f"time_posted ({e})")
    if filters.get("easy_apply"):
        try:
            apply_easy_apply_filter(page)
        except Exception as e:
            failures.append(f"easy_apply ({e})")

    if failures:
        print("Could not apply some filters:")
        for item in failures:
            print(f"- {item}")
        print("Please set these filters manually in the browser.")
        input("Press Enter to continue...")


def extract_job_id(card):
    job_id = card.get_attribute("data-occludable-job-id")
    if job_id:
        return job_id
    job_id = card.get_attribute("data-job-id")
    if job_id:
        return job_id
    try:
        href = card.locator("a").first.get_attribute("href")
        if href:
            m = re.search(r"/jobs/view/(\d+)", href)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


def get_job_title(page):
    selectors = [
        ".jobs-unified-top-card__job-title",
        "h1",
        "h2",
    ]
    for sel in selectors:
        loc = page.locator(sel)
        if loc.count() > 0 and loc.first.is_visible():
            text = loc.first.text_content()
            if text:
                return text.strip()
    return None


def get_job_company(page):
    selectors = [
        ".jobs-unified-top-card__company-name",
        ".job-details-jobs-unified-top-card__company-name",
        "a[data-control-name='company_link']",
    ]
    for sel in selectors:
        loc = page.locator(sel)
        if loc.count() > 0 and loc.first.is_visible():
            text = loc.first.text_content()
            if text:
                return text.strip()
    return None


def easy_apply_button(page):
    btn = page.locator("button:has-text('Easy Apply')")
    if btn.count() > 0:
        return btn.first
    return None


def is_modal_open(page) -> bool:
    return page.locator("div[role='dialog']").is_visible()


def wait_for_modal_close(page, max_wait_seconds=None):
    start = time.time()
    while is_modal_open(page):
        time.sleep(0.5)
        if max_wait_seconds and (time.time() - start) > max_wait_seconds:
            return False
    return True


def complete_easy_apply(page, behavior: dict) -> str:
    # Modal should already be open
    pause_on_unfilled = behavior.get("pause_on_unfilled", True)
    max_idle = behavior.get("max_idle_seconds", 900)

    last_action = time.time()

    while True:
        modal = page.locator("div[role='dialog']")
        if not modal.is_visible():
            return "closed"

        submit_btn = modal.locator("button:has-text('Submit')")
        if submit_btn.count() > 0 and submit_btn.first.is_visible():
            print("Ready to submit. Please review and click Submit in the modal.")
            if not wait_for_modal_close(page, max_idle):
                print("Timed out waiting for submit. Leaving modal open.")
                return "timeout"
            # Sometimes a Done button appears after submit
            done_btn = page.locator("button:has-text('Done')")
            if done_btn.count() > 0 and done_btn.first.is_visible():
                done_btn.first.click()
            return "submitted"

        review_btn = modal.locator("button:has-text('Review')")
        if review_btn.count() > 0 and review_btn.first.is_visible():
            review_btn.first.click()
            last_action = time.time()
            continue

        next_btn = modal.locator("button:has-text('Next')")
        if next_btn.count() > 0 and next_btn.first.is_visible():
            next_btn.first.click()
            last_action = time.time()
            time.sleep(0.5)
            # Detect validation errors
            error = modal.locator(".artdeco-inline-feedback__message")
            if error.count() > 0 and error.first.is_visible():
                if pause_on_unfilled:
                    print("Validation error. Fill required fields in the modal.")
                    input("Press Enter to continue...")
            continue

        # If none of the expected buttons are visible, ask user to complete manually
        if pause_on_unfilled:
            print("Please complete this step manually in the modal.")
            input("Press Enter to re-check...")
            last_action = time.time()
            continue

        # Safety fallback to avoid tight loop
        if time.time() - last_action > max_idle:
            return "timeout"
        time.sleep(0.5)


def main():
    config = load_config(CONFIG_PATH)
    filters = config.get("filters", {})
    behavior = config.get("behavior", {})
    state_path = config.get("state", {}).get("file", "state/applied.json")
    state = load_state(state_path)

    with sync_playwright() as p:
        print("Connecting to Chrome on http://localhost:9222 ...")
        browser = p.chromium.connect_over_cdp("http://localhost:9222")

        page = find_jobs_page(browser)
        while not page:
            prompt_retry("No LinkedIn Jobs tab found. Open https://www.linkedin.com/jobs/ in the debug Chrome.")
            page = find_jobs_page(browser)

        page.bring_to_front()
        page.set_default_timeout(5000)

        print("Applying filters...")
        apply_filters(page, filters)

        print("Starting job loop. Press Ctrl+C in the terminal to stop.")
        seen = set(state.get("jobs", {}).keys())

        while True:
            try:
                list_locator = page.locator("ul.jobs-search-results__list li")
                if list_locator.count() == 0:
                    print("No job cards found. Make sure the Jobs search results list is visible.")
                    time.sleep(3)
                    continue

                count = list_locator.count()
                for i in range(count):
                    card = list_locator.nth(i)
                    job_id = extract_job_id(card) or f"idx-{i}-{int(time.time())}"
                    if job_id in seen:
                        continue

                    try:
                        card.scroll_into_view_if_needed()
                        card.click()
                    except Exception:
                        continue

                    time.sleep(1)

                    title = get_job_title(page)
                    company = get_job_company(page)

                    btn = easy_apply_button(page)
                    if not btn:
                        state["jobs"][job_id] = {
                            "status": "skipped_no_easy_apply",
                            "title": title,
                            "company": company,
                            "url": page.url,
                            "updated_at": now_iso(),
                        }
                        save_state(state_path, state)
                        seen.add(job_id)
                        continue

                    try:
                        btn.click()
                    except Exception:
                        continue

                    time.sleep(1)
                    result = complete_easy_apply(page, behavior)

                    state["jobs"][job_id] = {
                        "status": result,
                        "title": title,
                        "company": company,
                        "url": page.url,
                        "updated_at": now_iso(),
                    }
                    save_state(state_path, state)
                    seen.add(job_id)

                # Scroll to load more results
                try:
                    results_container = page.locator("div.jobs-search-results-list")
                    if results_container.count() > 0:
                        results_container.first.evaluate("(el) => el.scrollBy(0, 1200)")
                    else:
                        page.mouse.wheel(0, 1200)
                except Exception:
                    page.mouse.wheel(0, 1200)
                time.sleep(1)
            except KeyboardInterrupt:
                print("Stopping...")
                break


if __name__ == "__main__":
    main()
