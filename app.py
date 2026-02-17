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


def apply_location_filter(page, location: str) -> bool:
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
                return True

        # As a last resort, click the first suggestion
        if suggestions.first.is_visible():
            suggestions.first.click()
            return True

    input_box.press("Enter")
    return True


def apply_date_posted_filter(page, label: str, use_all_filters: bool) -> bool:
    # Prefer All filters panel when enabled
    if use_all_filters:
        print("date_posted: using All filters panel")
        panel = open_filters_panel(page)
        if not panel:
            raise RuntimeError("Filters panel not found")

        value_map = {
            "past 24 hours": "r86400",
            "past week": "r604800",
            "past month": "r2592000",
            "any time": "",
        }
        label_key = label.strip().lower()
        value = value_map.get(label_key)

        date_section = panel.locator("fieldset").filter(
            has_text=re.compile("Date posted|Time posted", re.I)
        )
        if date_section.count() > 0:
            date_section.first.scroll_into_view_if_needed()

        # Prefer clicking the label inside the Date posted section
        label_loc = None
        if date_section.count() > 0:
            label_loc = date_section.locator("label").filter(
                has_text=re.compile(label, re.I)
            )
        if label_loc is None or label_loc.count() == 0:
            label_loc = panel.locator("label").filter(has_text=re.compile(label, re.I))

        if label_loc.count() > 0:
            label_loc.first.click()
        else:
            option = None
            if value is not None:
                scope = date_section if date_section.count() > 0 else panel
                option = scope.locator(
                    f"input[name='date-posted-filter-value'][value='{value}']"
                )
                if value == "":
                    option = scope.locator(
                        "input[name='date-posted-filter-value'][id='timePostedRange-']"
                    )

            if not option or option.count() == 0:
                option = panel.get_by_label(re.compile(label, re.I))

            if option.count() == 0:
                raise RuntimeError("Date posted option not found in filters panel")

            try:
                option.first.check(force=True)
            except Exception:
                option.first.click(force=True)

        return True

    # Top bar path
    container = page.locator(
        "div.search-reusables__filter-trigger-and-dropdown[data-basic-filter-parameter-name='timePostedRange']"
    )
    btn = None
    if container.count() > 0:
        btn = container.locator(
            "button#searchFilter_timePostedRange, button[aria-label*='Date posted filter' i]"
        )
        if btn.count() > 0:
            btn = btn.first

    if not btn:
        btn = find_date_posted_button(page)

    if not btn:
        print("date_posted: pill not found")
        raise RuntimeError("Top-bar Date posted not found")

    print("date_posted: pill found")
    btn.click()
    time.sleep(0.3)

    dropdown = page.locator(
        "div[role='listbox'], ul[role='listbox'], div[role='menu'], ul[role='menu']"
    )
    option = dropdown.get_by_role("menuitemradio", name=re.compile(label, re.I))
    if option.count() == 0:
        option = page.get_by_role("radio", name=re.compile(label, re.I))
    if option.count() == 0:
        option = page.get_by_label(re.compile(label, re.I))

    if option.count() == 0:
        raise RuntimeError("Date posted option not found in top bar")

    try:
        option.first.check(force=True)
    except Exception:
        option.first.click(force=True)
    return True


def apply_easy_apply_filter(page, use_all_filters: bool) -> bool:
    # Try direct filter button (top bar) first
    btn = find_top_filter_button(page, re.compile("Easy Apply", re.I))
    if btn:
        pressed = btn.get_attribute("aria-pressed")
        if pressed != "true":
            btn.click()
            time.sleep(0.3)
        return True

    if not use_all_filters:
        raise RuntimeError("Top-bar Easy Apply not found")

    panel = open_filters_panel(page)
    if not panel:
        raise RuntimeError("Filters panel not found")
    ensure_filter_section(panel, re.compile("Easy Apply|Kolay", re.I))
    checkbox = panel.get_by_role("checkbox", name=re.compile("Easy Apply|Kolay", re.I))
    if checkbox.count() == 0:
        checkbox = panel.locator("label:has-text('Easy Apply') input[type='checkbox']")
    if checkbox.count() == 0:
        checkbox = panel.locator("label:has-text('Kolay') input[type='checkbox']")
    if checkbox.count() == 0:
        # fallback: click the label/container that includes the text
        label = panel.locator("label, li, div").filter(
            has_text=re.compile("Easy Apply|Kolay", re.I)
        )
        if label.count() > 0:
            label.first.scroll_into_view_if_needed()
            label.first.click()
        else:
            raise RuntimeError("Easy Apply checkbox not found in filters panel")
    else:
        checkbox.first.scroll_into_view_if_needed()
        checkbox.first.check(force=True)
    panel.get_by_role("button", name=re.compile("Show results|Apply", re.I)).first.click()
    return True


def clear_distance_filter(page, use_all_filters: bool) -> bool:
    # Prefer the top-level Distance filter if present
    btn = find_top_filter_button(page, re.compile("^Distance$", re.I))
    if btn:
        btn.click()
        option = page.get_by_role("radio", name=re.compile("Any distance|Any", re.I))
        if option.count() == 0:
            option = page.get_by_label(re.compile("Any distance|Any", re.I))
        if option.count() > 0:
            try:
                option.first.check(force=True)
            except Exception:
                option.first.click(force=True)
        return True

    if not use_all_filters:
        return True

    panel = open_filters_panel(page)
    if not panel:
        raise RuntimeError("Filters panel not found")

    fieldset = ensure_filter_section(panel, re.compile("Distance", re.I))
    option = None
    if fieldset.count() > 0:
        option = fieldset.get_by_role("radio", name=re.compile("Any distance|Any", re.I))
        if option.count() == 0:
            option = fieldset.get_by_label(re.compile("Any distance|Any", re.I))

    if not option or option.count() == 0:
        option = panel.get_by_role("radio", name=re.compile("Any distance|Any", re.I))
        if option.count() == 0:
            option = panel.get_by_label(re.compile("Any distance|Any", re.I))

    if option.count() > 0:
        option.first.scroll_into_view_if_needed()
        try:
            option.first.check(force=True)
        except Exception:
            option.first.click(force=True)
        apply_btn = panel.get_by_role("button", name=re.compile("Show results|Apply", re.I))
        if apply_btn.count() > 0:
            apply_btn.first.click()
        return True
    return False


def open_filters_panel(page):
    candidates = [
        page.get_by_role("button", name=re.compile("^All filters$", re.I)),
        page.get_by_role("button", name=re.compile("^Filters$", re.I)),
        page.get_by_role("button", name=re.compile("All filters", re.I)),
        page.locator("button[aria-label*='All filters' i]"),
        page.locator("button[aria-label*='Filters' i]"),
        page.locator("button[data-control-name='all_filters']"),
    ]
    clicked = False
    for loc in candidates:
        try:
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click()
                clicked = True
                break
        except Exception:
            continue

    if not clicked:
        return None

    time.sleep(0.5)
    panel = page.locator("div[role='dialog']").filter(
        has=page.get_by_role("button", name=re.compile("Show results|Apply", re.I))
    )
    if panel.count() > 0:
        return panel.first

    panel = page.locator("section[aria-label*='Filters' i], aside[aria-label*='Filters' i]")
    if panel.count() > 0 and panel.first.is_visible():
        return panel.first

    return None


def ensure_filter_section(panel, name_regex):
    headers = panel.locator("button, summary").filter(has_text=name_regex)
    if headers.count() > 0:
        header = headers.first
        try:
            expanded = header.get_attribute("aria-expanded")
            if expanded == "false":
                header.click()
        except Exception:
            try:
                header.click()
            except Exception:
                pass

    fieldset = panel.locator("fieldset").filter(has_text=name_regex)
    if fieldset.count() > 0:
        fieldset.first.scroll_into_view_if_needed()
    return fieldset


def find_top_filter_button(page, name_regex):
    containers = [
        page.locator("ul.search-reusables__filter-list"),
        page.locator("ul.search-reusables__filters-list"),
        page.locator("div.search-reusables__filters-bar"),
        page.locator("div.jobs-search-filters__filter-list"),
        page.locator("div.jobs-search-filters__filters"),
        page.locator("div.jobs-search-filters__filters-bar"),
        page.locator("div.jobs-search-filters"),
        page.locator("section.jobs-search-filters"),
    ]
    for container in containers:
        if container.count() > 0:
            btn = container.get_by_role("button", name=name_regex)
            if btn.count() > 0 and btn.first.is_visible():
                return btn.first
            btn = container.locator("button, span, div").filter(has_text=name_regex)
            if btn.count() > 0 and btn.first.is_visible():
                candidate = btn.first
                try:
                    if candidate.get_attribute("role") != "button":
                        ancestor = candidate.locator("xpath=ancestor::button[1]")
                        if ancestor.count() > 0:
                            return ancestor.first
                        ancestor = candidate.locator("xpath=ancestor::*[@role='button'][1]")
                        if ancestor.count() > 0:
                            return ancestor.first
                except Exception:
                    pass
                return candidate
            text_match = container.get_by_text(name_regex)
            if text_match.count() > 0 and text_match.first.is_visible():
                candidate = text_match.first
                try:
                    ancestor = candidate.locator("xpath=ancestor-or-self::button[1]")
                    if ancestor.count() > 0 and ancestor.first.is_visible():
                        return ancestor.first
                    ancestor = candidate.locator("xpath=ancestor-or-self::*[@role='button'][1]")
                    if ancestor.count() > 0 and ancestor.first.is_visible():
                        return ancestor.first
                except Exception:
                    pass
                return candidate

    btn = page.get_by_role("button", name=name_regex)
    if btn.count() > 0 and btn.first.is_visible():
        return btn.first
    btn = page.locator("button[aria-label]").filter(has_text=name_regex)
    if btn.count() > 0 and btn.first.is_visible():
        return btn.first
    btn = page.locator("button").filter(has_text=name_regex)
    if btn.count() > 0 and btn.first.is_visible():
        return btn.first
    return None


def find_filters_bar_container(page):
    containers = [
        page.locator("ul.search-reusables__filter-list"),
        page.locator("ul.search-reusables__filters-list"),
        page.locator("div.search-reusables__filters-bar"),
        page.locator("div.jobs-search-filters__filter-list"),
        page.locator("div.jobs-search-filters__filters"),
        page.locator("div.jobs-search-filters__filters-bar"),
        page.locator("div.jobs-search-filters"),
        page.locator("section.jobs-search-filters"),
    ]
    for container in containers:
        if container.count() == 0:
            continue
        # Prefer containers that include a known filter like "All filters" or "Easy Apply"
        if (
            container.get_by_role("button", name=re.compile("All filters|Easy Apply", re.I)).count()
            > 0
            or container.locator("button").filter(
                has_text=re.compile("All filters|Easy Apply", re.I)
            ).count()
            > 0
        ):
            return container
    return None


def find_date_posted_button(page):
    name_regex = re.compile("Date posted|Time posted", re.I)
    container = find_filters_bar_container(page)
    if container:
        btn = container.get_by_role("button", name=name_regex)
        if btn.count() > 0 and btn.first.is_visible():
            return btn.first
        btn = container.locator("[role='button']").filter(has_text=name_regex)
        if btn.count() > 0 and btn.first.is_visible():
            return btn.first
        btn = container.locator("button").filter(has_text=name_regex)
        if btn.count() > 0 and btn.first.is_visible():
            return btn.first
        btn = container.locator("span, div").filter(has_text=name_regex)
        if btn.count() > 0 and btn.first.is_visible():
            candidate = btn.first
            try:
                ancestor = candidate.locator("xpath=ancestor-or-self::button[1]")
                if ancestor.count() > 0 and ancestor.first.is_visible():
                    return ancestor.first
                ancestor = candidate.locator("xpath=ancestor-or-self::*[@role='button'][1]")
                if ancestor.count() > 0 and ancestor.first.is_visible():
                    return ancestor.first
            except Exception:
                pass
            return candidate

    # Fallback: visible button with label
    btn = page.get_by_role("button", name=name_regex)
    if btn.count() > 0 and btn.first.is_visible():
        return btn.first
    btn = page.locator("[role='button']").filter(has_text=name_regex)
    if btn.count() > 0 and btn.first.is_visible():
        return btn.first
    return None


def apply_filters(page, filters: dict) -> None:
    failures = []
    use_all_filters = filters.get("use_all_filters", True)
    wait_after_each = filters.get("wait_after_each_seconds", 0)
    wait_after_location = filters.get("wait_after_location_seconds", wait_after_each)
    if filters.get("location"):
        try:
            ok = apply_location_filter(page, filters["location"])
            print(f"location: {'applied' if ok else 'not applied'}")
            wait_for_results_refresh(page, wait_after_location)
        except Exception as e:
            print(f"location: failed ({e})")
            failures.append(f"location ({e})")
    # Clear distance when not explicitly set
    if "distance" in filters and not filters.get("distance"):
        try:
            clear_distance_filter(page, use_all_filters)
        except Exception as e:
            failures.append(f"distance ({e})")
    if filters.get("time_posted"):
        try:
            ok = apply_date_posted_filter(page, filters["time_posted"], use_all_filters)
            print(f"time_posted: {'applied' if ok else 'not applied'}")
            wait_for_results_refresh(page, wait_after_each)
        except Exception as e:
            print(f"time_posted: failed ({e})")
            failures.append(f"time_posted ({e})")
    if filters.get("easy_apply"):
        try:
            ok = apply_easy_apply_filter(page, use_all_filters)
            ok = apply_easy_apply_filter(page, use_all_filters)
            print(f"easy_apply: {'applied' if ok else 'not applied'}")
            wait_for_results_refresh(page, wait_after_each)
        except Exception as e:
            print(f"easy_apply: failed ({e})")
            failures.append(f"easy_apply ({e})")

    if failures:
        print("Could not apply some filters:")
        for item in failures:
            print(f"- {item}")
        print("Please set these filters manually in the browser.")
        input("Press Enter to continue...")


def get_job_cards(page):
    selectors = [
        "ul.jobs-search-results__list li",
        "div.jobs-search-results-list ul li",
        "div.jobs-search-results-list__content ul li",
        "ul.scaffold-layout__list-container li",
        "div.scaffold-layout__list-detail-inner ul li",
        "li[data-occludable-job-id]",
        "li[data-job-id]",
        "div.job-card-container",
        "div[data-occludable-job-id]",
        "div[data-job-id]",
    ]
    for sel in selectors:
        loc = page.locator(sel)
        if loc.count() > 0:
            return loc
    return page.locator("ul.jobs-search-results__list li")  # default empty locator


def get_results_container(page):
    selectors = [
        "div.jobs-search-results-list",
        "div.jobs-search-results-list__content",
        "ul.jobs-search-results__list",
        "div.scaffold-layout__list-detail",
        "div.scaffold-layout__list-detail-inner",
    ]
    for sel in selectors:
        loc = page.locator(sel)
        if loc.count() > 0 and loc.first.is_visible():
            return loc.first
    return None


def is_recently_applied_card(card) -> bool:
    try:
        text = card.text_content() or ""
    except Exception:
        return False
    t = " ".join(text.split()).lower()
    # Match: "Applied 5 minutes ago", "Applied 2 hours ago", "Applied a few minutes ago"
    if re.search(r"\bapplied\b.*\bago\b", t):
        if re.search(r"\b(\d+|a|an|few)\b.*\b(minute|minutes|hour|hours)\b", t):
            return True
        if re.search(r"\b\d+\s*(m|h)\b", t):
            return True
    return False


def wait_for_results_refresh(page, min_wait_seconds: float) -> None:
    if min_wait_seconds:
        time.sleep(min_wait_seconds)

    list_locator = page.locator("ul.jobs-search-results__list li")
    before = None
    try:
        if list_locator.count() > 0:
            before = list_locator.nth(0).text_content()
    except Exception:
        before = None

    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass

    if before:
        end_time = time.time() + 5
        while time.time() < end_time:
            try:
                if list_locator.count() > 0:
                    after = list_locator.nth(0).text_content()
                    if after and after != before:
                        break
            except Exception:
                pass
            time.sleep(0.3)


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
    label_re = re.compile("Easy Apply|Kolay", re.I)
    containers = [
        "div.jobs-details__main-content",
        "div.jobs-details__container",
        "div.jobs-search__job-details",
        "div.jobs-search__job-details--wrapper",
        "section.jobs-details-top-card",
        "div.jobs-unified-top-card",
        "main#main",
    ]
    for sel in containers:
        container = page.locator(sel)
        if container.count() == 0:
            continue
        btn = container.locator("button").filter(has_text=label_re)
        if btn.count() > 0:
            primary = btn.filter(has=page.locator(".artdeco-button--primary"))
            if primary.count() > 0:
                return primary.first
            # prefer buttons that look like apply action
            apply_btn = btn.filter(has=page.locator("span")).filter(
                has_text=label_re
            )
            if apply_btn.count() > 0:
                return apply_btn.first
            return btn.first

    # Fallback: specific class used for apply buttons
    btn = page.locator("button.jobs-apply-button").filter(has_text=label_re)
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


def click_done_if_present(page, timeout_ms=8000) -> bool:
    done_btn = page.locator("button:has-text('Done')")
    try:
        if done_btn.count() == 0:
            done_btn = page.get_by_role("button", name=re.compile("Done|Bitti|Tamam", re.I))
        done_btn.first.wait_for(state="visible", timeout=timeout_ms)
        done_btn.first.click()
        return True
    except Exception:
        return False


def auto_fill_defaults_in_modal(modal, defaults: dict) -> None:
    salary = defaults.get("salary")
    if not salary:
        return

    keywords = [
        "salary",
        "compensation",
        "pay",
        "expected salary",
        "desired salary",
        "salary expectation",
        "annual",
        "monthly",
        "hourly",
        "rate",
        "wage",
        "maaş",
        "maas",
        "ücret",
        "ucret",
        "maaş beklentisi",
        "ucret beklentisi",
    ]
    kw_re = re.compile("|".join(re.escape(k) for k in keywords), re.I)

    def fill_input(input_locator):
        try:
            current = input_locator.input_value()
        except Exception:
            current = ""
        if current and str(current).strip():
            return False
        input_locator.fill(str(salary))
        return True

    # 1) Inputs with aria-label
    inputs = modal.locator("input[type='text'], input[type='number'], textarea")
    for i in range(inputs.count()):
        inp = inputs.nth(i)
        try:
            aria = inp.get_attribute("aria-label") or ""
        except Exception:
            aria = ""
        if aria and kw_re.search(aria):
            if fill_input(inp):
                print(f"salary: filled via aria-label '{aria}'")

    # 2) Label -> input via for=
    labels = modal.locator("label")
    for i in range(labels.count()):
        lbl = labels.nth(i)
        try:
            text = (lbl.text_content() or "").strip()
        except Exception:
            text = ""
        if not text or not kw_re.search(text):
            continue
        try:
            target_id = lbl.get_attribute("for")
        except Exception:
            target_id = None
        if target_id:
            inp = modal.locator(f"#{target_id}")
            if inp.count() > 0:
                if fill_input(inp.first):
                    print(f"salary: filled via label '{text}'")


def ensure_follow_company_unchecked(modal) -> None:
    # Direct selector from observed DOM
    cb = modal.locator("#follow-company-checkbox")
    if cb.count() == 0:
        cb = modal.page.locator("#follow-company-checkbox")
    if cb.count() > 0:
        try:
            state = cb.first.evaluate(
                "el => ({checked: el.checked, attr: el.getAttribute('checked')})"
            )
        except Exception:
            state = {"checked": None, "attr": None}
        if state.get("checked") is True:
            try:
                result = cb.first.evaluate(
                    "el => { el.checked = false; "
                    "el.dispatchEvent(new Event('input', {bubbles:true})); "
                    "el.dispatchEvent(new Event('change', {bubbles:true})); "
                    "return el.checked; }"
                )
                print(f"follow_company: set checked false -> {result}")
            except Exception:
                pass
        # If still checked, click label to toggle
        try:
            if cb.first.is_checked():
                lbl = modal.locator("label[for='follow-company-checkbox']")
                if lbl.count() == 0:
                    lbl = modal.page.locator("label[for='follow-company-checkbox']")
                if lbl.count() > 0:
                    lbl.first.click(force=True)
                    time.sleep(0.1)
            if cb.first.is_checked():
                # Last resort: click the input directly
                cb.first.click(force=True)
                time.sleep(0.1)
        except Exception:
            pass
        try:
            final_checked = cb.first.is_checked()
        except Exception:
            final_checked = None
        print(f"follow_company: final checked={final_checked}")
        if final_checked is False:
            return

    keywords = [
        "follow",
        "company",
        "employer",
        "follow the company",
        "follow company",
        "stay up to date",
        "işvereni takip",
        "şirketi takip",
        "sirketi takip",
        "takip et",
    ]
    kw_re = re.compile("|".join(re.escape(k) for k in keywords), re.I)

    # Prefer role-based checkbox (often used in LinkedIn modals)
    role_cb = modal.get_by_role("checkbox", name=re.compile("follow", re.I))
    if role_cb.count() > 0:
        cb = role_cb.first
        try:
            aria_checked = cb.get_attribute("aria-checked")
        except Exception:
            aria_checked = None
        try:
            checked = cb.is_checked()
        except Exception:
            checked = None
        if aria_checked == "true" or checked is True:
            cb.click(force=True)
            print("follow_company: unchecked via role=checkbox")
            return

    # Explicit input checkbox with aria-label
    checkboxes = modal.locator("input[type='checkbox']")
    for i in range(checkboxes.count()):
        cb = checkboxes.nth(i)
        try:
            aria = cb.get_attribute("aria-label") or ""
        except Exception:
            aria = ""
        if aria and kw_re.search(aria):
            try:
                checked = cb.is_checked()
            except Exception:
                checked = None
            if checked is True:
                cb.uncheck(force=True)
                print("follow_company: unchecked via aria-label")
            return

    # Look for labels containing keywords
    labels = modal.locator("label")
    for i in range(labels.count()):
        lbl = labels.nth(i)
        try:
            text = (lbl.text_content() or "").strip()
        except Exception:
            text = ""
        if not text or not kw_re.search(text):
            continue
        # Try direct for= link
        target_id = lbl.get_attribute("for")
        if target_id:
            cb = modal.locator(f"#{target_id}")
            if cb.count() > 0:
                try:
                    checked = cb.first.is_checked()
                except Exception:
                    checked = None
                if checked is True:
                    cb.first.uncheck(force=True)
                    print("follow_company: unchecked via label for=")
                return
        # Fallback: nested checkbox or role checkbox
        cb = lbl.locator("input[type='checkbox']")
        if cb.count() > 0:
            try:
                checked = cb.first.is_checked()
            except Exception:
                checked = None
            if checked is True:
                lbl.click(force=True)
                print("follow_company: unchecked via nested checkbox")
            return
        role_cb = lbl.get_by_role("checkbox")
        if role_cb.count() > 0:
            try:
                aria_checked = role_cb.first.get_attribute("aria-checked")
            except Exception:
                aria_checked = None
            if aria_checked == "true":
                role_cb.first.click(force=True)
                print("follow_company: unchecked via label role=checkbox")
            return

def complete_easy_apply(page, behavior: dict, defaults: dict) -> str:
    # Modal should already be open
    pause_on_unfilled = behavior.get("pause_on_unfilled", True)
    max_idle = behavior.get("max_idle_seconds", 900)
    auto_submit = behavior.get("auto_submit", False)

    last_action = time.time()

    while True:
        modal = page.locator("div[role='dialog']")
        if not modal.is_visible():
            return "closed"

        try:
            auto_fill_defaults_in_modal(modal, defaults)
        except Exception:
            pass

        try:
            ensure_follow_company_unchecked(modal)
        except Exception:
            pass

        submit_btn = modal.locator("button:has-text('Submit')")
        if submit_btn.count() > 0 and submit_btn.first.is_visible():
            if auto_submit:
                print("Auto-submitting application...")
                time.sleep(2)
                submit_btn.first.click()
                time.sleep(0.5)
                # Check for validation errors after submit
                error = modal.locator(".artdeco-inline-feedback__message")
                if error.count() > 0 and error.first.is_visible():
                    if pause_on_unfilled:
                        print("Validation error after submit. Fill required fields in the modal.")
                        input("Press Enter to continue...")
                    continue
                # Click Done if the post-submit screen appears
                click_done_if_present(page)
                if not wait_for_modal_close(page, max_idle):
                    print("Timed out waiting after submit. Leaving modal open.")
                    return "timeout"
                done_btn = page.locator("button:has-text('Done')")
                if done_btn.count() > 0 and done_btn.first.is_visible():
                    done_btn.first.click()
                return "submitted"
            else:
                print("Ready to submit. Please review and click Submit in the modal.")
                # After user submits, click Done if it appears
                click_done_if_present(page)
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
    behavior = config.get("behavior", {})
    defaults = config.get("defaults", {})
    state_path = config.get("state", {}).get("file", "state/applied.json")
    state = load_state(state_path)
    refresh_after_submitted = behavior.get("refresh_after_submitted", None)
    submitted_since_refresh = 0

    with sync_playwright() as p:
        print("Connecting to Chrome on http://localhost:9222 ...")
        browser = p.chromium.connect_over_cdp("http://localhost:9222")

        page = find_jobs_page(browser)
        while not page:
            prompt_retry("No LinkedIn Jobs tab found. Open https://www.linkedin.com/jobs/ in the debug Chrome.")
            page = find_jobs_page(browser)

        page.bring_to_front()
        page.set_default_timeout(5000)

        print("Starting job loop. Press Ctrl+C in the terminal to stop.")
        seen = set(state.get("jobs", {}).keys())

        while True:
            try:
                list_locator = get_job_cards(page)
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
                    if is_recently_applied_card(card):
                        state["jobs"][job_id] = {
                            "status": "skipped_recently_applied",
                            "title": None,
                            "company": None,
                            "url": page.url,
                            "updated_at": now_iso(),
                        }
                        save_state(state_path, state)
                        seen.add(job_id)
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
                    result = complete_easy_apply(page, behavior, defaults)

                    state["jobs"][job_id] = {
                        "status": result,
                        "title": title,
                        "company": company,
                        "url": page.url,
                        "updated_at": now_iso(),
                    }
                    save_state(state_path, state)
                    seen.add(job_id)
                    if result == "submitted" and refresh_after_submitted:
                        submitted_since_refresh += 1
                        if submitted_since_refresh >= int(refresh_after_submitted):
                            print(f"Refreshing page after {submitted_since_refresh} submissions...")
                            try:
                                page.reload(wait_until="domcontentloaded")
                                time.sleep(2)
                            except Exception:
                                pass
                            submitted_since_refresh = 0

                # Scroll to load more results
                try:
                    results_container = get_results_container(page)
                    if results_container:
                        results_container.evaluate("(el) => el.scrollBy(0, 1200)")
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
