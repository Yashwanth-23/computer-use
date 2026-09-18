from dataclasses import dataclass
from typing import List, Optional
from playwright.sync_api import Page


@dataclass
class ObservedElement:
    index: int
    tag: str
    element_id: Optional[str]
    name_attr: Optional[str]
    role: Optional[str]
    accessible_name: Optional[str]
    text_content: Optional[str]
    selector: str
    xpath: str


class SurfaceObserver:
    """Extracts a structured, accessible representation of interactive elements on a live surface."""

    JS_EXTRACTOR = """
    () => {
        const elements = [];
        const candidates = document.querySelectorAll('input, button, select, a, textarea, [role], [id^="ctl00"], .banner');
        let idx = 1;

        for (let i = 0; i < candidates.length; i++) {
            const el = candidates[i];
            const rect = el.getBoundingClientRect();
            const isVisible = rect.width > 0 && rect.height > 0 && window.getComputedStyle(el).display !== 'none';
            if (!isVisible) continue;

            let role = el.getAttribute('role') || el.tagName.toLowerCase();
            if (el.tagName === 'INPUT') {
                const type = (el.getAttribute('type') || 'text').toLowerCase();
                role = (type === 'submit' || type === 'button') ? 'button' : 'textbox';
            }

            let accName = '';
            if (el.id) {
                const label = document.querySelector('label[for="' + el.id + '"]');
                if (label) accName = label.innerText.trim();
            }
            if (!accName && el.getAttribute('aria-label')) {
                accName = el.getAttribute('aria-label').trim();
            }
            if (!accName && el.innerText) {
                accName = el.innerText.trim().slice(0, 50);
            }

            let xpath = '';
            let current = el;
            while (current && current.nodeType === Node.ELEMENT_NODE) {
                let count = 1;
                let sibling = current.previousElementSibling;
                while (sibling) {
                    if (sibling.nodeName === current.nodeName) count++;
                    sibling = sibling.previousElementSibling;
                }
                xpath = '/' + current.nodeName.toLowerCase() + '[' + count + ']' + xpath;
                current = current.parentElement;
                if (current && current.nodeName === 'BODY') {
                    xpath = '//body' + xpath;
                    break;
                }
            }

            elements.push({
                index: idx++,
                tag: el.tagName.toLowerCase(),
                element_id: el.id || null,
                name_attr: el.getAttribute('name') || null,
                role: role,
                accessible_name: accName || null,
                text_content: (el.innerText || el.value || '').trim().slice(0, 80) || null,
                selector: el.id ? '#' + el.id : (el.name ? '[name="' + el.name + '"]' : el.tagName.toLowerCase()),
                xpath: xpath
            });
        }
        return elements;
    }
    """

    def observe(self, page: Page) -> List[ObservedElement]:
        """Scans the page and returns a list of interactive elements."""
        raw_items = page.evaluate(self.JS_EXTRACTOR)
        observed = []
        for item in raw_items:
            observed.append(ObservedElement(**item))
        return observed

    def format_for_prompt(self, page: Page, elements: List[ObservedElement]) -> str:
        """Formats the observed elements into a concise textual prompt for the LLM."""
        lines = [f"PAGE URL: {page.url}", f"PAGE TITLE: {page.title()}", "INTERACTIVE ELEMENTS:"]
        for el in elements:
            parts = [f"[#{el.index}] <{el.tag}> (role={el.role})"]
            if el.element_id:
                parts.append(f'id="{el.element_id}"')
            if el.name_attr:
                parts.append(f'name="{el.name_attr}"')
            if el.accessible_name:
                parts.append(f'label/name="{el.accessible_name}"')
            if el.text_content:
                parts.append(f'text="{el.text_content}"')
            lines.append("  " + " ".join(parts))
        return "\n".join(lines)
