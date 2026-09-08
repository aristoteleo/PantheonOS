"""Actual DOM selectors must hit controls, not matching explanatory text."""

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from pantheon.apps.builtin.desktop.browser_snapshot import BROWSER_SNAPSHOT_JS
from pantheon.apps.builtin.desktop.toolset import DesktopToolSet


@pytest.fixture
async def live_page():
    playwright = pytest.importorskip("playwright.async_api")
    executable = os.environ.get("PANTHEON_TEST_CHROME")
    local_chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if not executable and local_chrome.exists():
        executable = str(local_chrome)
    async with playwright.async_playwright() as driver:
        try:
            browser = await driver.chromium.launch(
                headless=True, **({"executable_path": executable} if executable else {}))
        except playwright.Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("Install Playwright Chromium to run DOM integration tests")
            raise
        try:
            yield await browser.new_page(viewport={"width": 960, "height": 500})
        finally:
            await browser.close()


def tools_for_page(page, monkeypatch):
    tools = DesktopToolSet()

    async def call(coroutine):
        return await coroutine

    session = SimpleNamespace(id="owned-native-page", page=page, url=page.url, title=page.title)
    monkeypatch.setattr(tools, "_browser_engine", lambda: SimpleNamespace(call=call))
    resolve = AsyncMock(return_value=session)
    monkeypatch.setattr(tools, "_resolve_control_page", resolve)
    return tools, resolve


async def test_observed_selectors_complete_form_despite_ambiguous_text(live_page, monkeypatch):
    await live_page.set_content("""
      <p>Click Begin, enter confirmation, then Submit.</p>
      <button id="begin" onclick="this.disabled=true">Begin</button>
      <form onsubmit="event.preventDefault(); document.querySelector('output').textContent='Result: '+this.querySelector('input').value">
        <label for="confirmation">Confirmation text</label><input id="confirmation">
        <button>Submit</button>
      </form><output></output>
    """)
    tools, resolve = tools_for_page(live_page, monkeypatch)
    before = await live_page.content()
    read = await tools.browser_read(page_id="#app:win-105")
    assert read["success"] and read["page_id"] == "owned-native-page"
    assert await live_page.content() == before  # observation must not inject attributes
    by_name = {element["name"]: element for element in read["elements"]}
    assert by_name["Confirmation text"]["selector"] == "#confirmation"
    for name in ("Begin", "Submit"):
        assert await live_page.locator(by_name[name]["selector"]).count() == 1
        assert await live_page.locator(by_name[name]["selector"]).evaluate("e => e.tagName") == "BUTTON"
    assert (await tools.browser_click(by_name["Begin"]["selector"], "#app:win-105"))["success"]
    assert (await tools.browser_type(by_name["Confirmation text"]["selector"], "unique input", page_id="#app:win-105"))["success"]
    assert (await tools.browser_click(by_name["Submit"]["selector"], "#app:win-105"))["success"]
    after = await tools.browser_read(page_id="#app:win-105")
    assert "Result: unique input" in after["text"]
    assert next(e for e in after["elements"] if e["name"] == "Begin")["disabled"]
    assert all(call.args[1] == "#app:win-105" for call in resolve.await_args_list)


async def test_unique_escaped_selectors_and_field_states(live_page):
    await live_page.set_content("""
      <button id="duplicate">First</button><section><button id="duplicate">Second</button></section>
      <input id="field:with spaces" aria-labelledby="label"><span id="label">Search terms</span>
      <label>Password<input type="password" value="do-not-expose"></label>
      <label>Upload<input type="file"></label>
      <label>Selected<input type="checkbox" checked></label>
      <input type="hidden" value="hidden-secret"><button style="display:none">Hidden</button>
      <div inert><button>Inert</button></div><button disabled>Disabled</button>
      <select aria-label="Choice"><option value="a">Alpha</option><option value="b" selected>Beta</option></select>
      <a href="https://example.test/paper">Paper</a>
    """)
    result = await live_page.evaluate(BROWSER_SNAPSHOT_JS, {"textLimit": 8000, "elementLimit": 100})
    elements = result["elements"]
    for element in elements:
        assert await live_page.locator(element["selector"]).count() == 1
        assert await live_page.locator(element["selector"]).evaluate("e => e.localName") == element["tag"]
    by_name = {element["name"]: element for element in elements}
    assert by_name["First"]["selector"] != by_name["Second"]["selector"]
    assert "value" not in by_name["Password"] and "value" not in by_name["Upload"]
    assert by_name["Selected"]["checked"] and by_name["Disabled"]["disabled"]
    assert by_name["Choice"]["value"] == "b"
    assert by_name["Paper"]["href"] == "https://example.test/paper"
    assert "Hidden" not in by_name and "Inert" not in by_name
    assert "hidden-secret" not in str(result) and "do-not-expose" not in str(result)


async def test_bounded_snapshot_prioritizes_controls_on_screen(live_page, monkeypatch):
    await live_page.set_content(
        '<p>' + 'Long text ' * 1500 + '</p>'
        + ''.join(f'<button>Offscreen {i}</button>' for i in range(110))
        + '<button style="position:fixed;top:0;left:0">Visible action</button>')
    tools, _ = tools_for_page(live_page, monkeypatch)
    result = await tools.browser_read(page_id="owned-native-page")
    assert result["success"]
    assert result["text"].endswith("… (truncated)")
    assert len(result["text"]) < 8100
    assert len(result["elements"]) == 100 and result["elements_truncated"]
    assert result["elements"][0]["name"] == "Visible action"
    assert result["elements"][0]["in_viewport"]
    assert result["elements_scope"] == "main_document"


async def test_empty_page_has_explicit_empty_observation(live_page, monkeypatch):
    tools, _ = tools_for_page(live_page, monkeypatch)
    result = await tools.browser_read(page_id="owned-native-page")
    assert result["success"]
    assert result["text"] == "" and result["elements"] == []
    assert result["elements_truncated"] is False
