"""Read-only DOM observations with selectors the Browser tools can actually use."""

# Keep the snapshot in the same evaluation as its text: selectors describe this
# page at observation time, not a separately fetched or reconstructed document.
# No synthetic IDs/listeners are injected into the user's page.
BROWSER_SNAPSHOT_JS = r"""({textLimit, elementLimit, elementTextLimit = 24000}) => {
    const clean = value => String(value || '').replace(/\s+/g, ' ').trim().slice(0, 200);
    const selectorFor = element => {
        if (element.id && element.id.length <= 128) {
            const selector = '#' + CSS.escape(element.id);
            if (selector.length <= 512 && document.querySelectorAll(selector).length === 1) return 'css:light=' + selector;
        }
        const parts = [];
        for (let node = element; node && node.nodeType === 1; node = node.parentElement) {
            const tag = CSS.escape(node.localName);
            const siblings = node.parentElement ? Array.from(node.parentElement.children) : [node];
            parts.unshift(tag + ':nth-child(' + (siblings.indexOf(node) + 1) + ')');
            const selector = parts.join(' > ');
            if (selector.length > 512) return null;
            if (document.querySelectorAll(selector).length === 1) return 'css:light=' + selector;
        }
        return null;
    };
    const nameFor = element => {
        const labelled = (element.getAttribute('aria-labelledby') || '').split(/\s+/)
            .map(id => document.getElementById(id)?.textContent || '').join(' ').trim();
        return clean(labelled || element.getAttribute('aria-label') ||
            Array.from(element.labels || []).map(label => label.innerText).join(' ') ||
            element.innerText || element.getAttribute('alt') ||
            (['submit', 'button', 'reset'].includes(element.type) ? element.value : '') ||
            element.getAttribute('placeholder') || element.getAttribute('title'));
    };
    const candidates = document.querySelectorAll(
        'button, input:not([type="hidden"]), textarea, select, a[href], summary, ' +
        '[contenteditable]:not([contenteditable="false"]), [tabindex], ' +
        '[role="button"], [role="link"], [role="textbox"], [role="checkbox"], ' +
        '[role="radio"], [role="combobox"], [role="menuitem"], [role="tab"], [role="switch"]'
    );
    const visible = [];
    for (const element of candidates) {
        const style = getComputedStyle(element);
        if (!element.getClientRects().length || style.visibility === 'hidden' ||
            style.visibility === 'collapse' || element.closest('[inert]')) continue;
        const rect = element.getBoundingClientRect();
        if (!rect.width || !rect.height) continue;
        visible.push({element, inViewport: rect.bottom > 0 && rect.right > 0 &&
            rect.top < innerHeight && rect.left < innerWidth});
    }
    // Visible controls come first; offscreen controls remain usable because
    // Playwright can scroll them into view before acting.
    visible.sort((a, b) => Number(b.inViewport) - Number(a.inViewport));
    let truncated = visible.length > elementLimit;
    const described = visible.slice(0, elementLimit).map(({element, inViewport}) => {
        const selector = selectorFor(element);
        if (!selector) { truncated = true; return null; }
        const item = {selector, tag: element.localName,
            name: nameFor(element), in_viewport: inViewport,
            disabled: element.matches(':disabled') || element.getAttribute('aria-disabled') === 'true'};
        const role = element.getAttribute('role');
        if (role) item.role = clean(role);
        if (element.localName === 'a' && element.href.length <= 2048) item.href = element.href;
        if (element.type) item.type = element.type;
        // Do not expose password values or local upload paths in observations.
        if ('value' in element && !['password', 'file'].includes(element.type)) item.value = clean(element.value);
        if (['checkbox', 'radio'].includes(element.type)) item.checked = element.checked;
        if (element.localName === 'select') item.options = Array.from(element.options).slice(0, 30)
            .map(option => ({label: clean(option.label), value: clean(option.value), selected: option.selected}));
        return item;
    });
    const elements = [];
    let remaining = elementTextLimit;
    for (const item of described) {
        if (!item) continue;
        const size = JSON.stringify(item).length + 1;
        if (size > remaining) { truncated = true; continue; }
        elements.push(item);
        remaining -= size;
    }
    return {text: (document.body?.innerText || '').slice(0, textLimit + 1), elements,
        elements_truncated: truncated, elements_scope: 'main_document'};
}"""

ELEMENT_LIMIT = 100
