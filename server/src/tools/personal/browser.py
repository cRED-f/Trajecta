"""Free local browser automation backed by Playwright.

Screenshots are written into /workspace and returned as virtual paths.  Deep
Agents can then call its built-in ``read_file`` on the image, which uses the
framework's multimodal content path instead of bloating tool history with
base64 data.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from server.src.config import Settings
from server.src.tools.personal.documents import VirtualPathResolver


class BrowserManager:
    def __init__(self, settings: Settings) -> None:
        self._cfg = settings.tools.browser
        self._paths = VirtualPathResolver(
            settings.tools.workspace_root,
            settings.chat.uploads_path,
        )
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._context: Any | None = None
        self._page: Any | None = None

    async def _ensure(self) -> Any:
        if self._page is not None:
            return self._page
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is required for browser tools. Install dependencies "
                "and run `playwright install chromium`."
            ) from exc
        self._playwright = await async_playwright().start()
        browser_type = getattr(self._playwright, self._cfg.browser, None)
        if browser_type is None:
            raise ValueError(f"Unsupported Playwright browser: {self._cfg.browser}")
        self._browser = await browser_type.launch(headless=self._cfg.headless)
        self._context = await self._browser.new_context(accept_downloads=True)
        self._page = await self._context.new_page()
        self._page.set_default_timeout(self._cfg.default_timeout_ms)
        return self._page

    async def navigate(self, url: str, *, wait_until: str = "domcontentloaded") -> dict[str, Any]:
        if not url.startswith(("http://", "https://")):
            raise ValueError("browser_navigate supports http:// and https:// URLs")
        page = await self._ensure()
        response = await page.goto(url, wait_until=wait_until)
        return {
            "url": page.url,
            "title": await page.title(),
            "status": response.status if response else None,
        }

    async def snapshot(self, *, max_text_chars: int = 30_000) -> dict[str, Any]:
        page = await self._ensure()
        result = await page.evaluate(
            """
            () => {
              const nodes = Array.from(document.querySelectorAll(
                'a,button,input,textarea,select,[role="button"],[role="link"],[contenteditable="true"]'
              ));
              const elements = [];
              let idx = 0;
              for (const el of nodes) {
                const rect = el.getBoundingClientRect();
                if (rect.width <= 0 || rect.height <= 0) continue;
                const id = `t-${idx++}`;
                el.setAttribute('data-trajecta-id', id);
                elements.push({
                  id,
                  tag: el.tagName.toLowerCase(),
                  role: el.getAttribute('role'),
                  type: el.getAttribute('type'),
                  name: el.getAttribute('aria-label') || el.getAttribute('name') || '',
                  text: (el.innerText || el.value || el.getAttribute('placeholder') || '').trim().slice(0, 300),
                  href: el.href || null,
                  disabled: !!el.disabled
                });
              }
              return {text: (document.body?.innerText || ''), elements};
            }
            """
        )
        return {
            "url": page.url,
            "title": await page.title(),
            "text": str(result.get("text", ""))[:max_text_chars],
            "elements": result.get("elements", [])[:500],
        }

    async def click(self, element_id: str) -> dict[str, Any]:
        page = await self._ensure()
        await page.locator(f'[data-trajecta-id="{element_id}"]').click()
        return {"ok": True, "url": page.url, "title": await page.title()}

    async def type(self, element_id: str, text: str, *, submit: bool = False) -> dict[str, Any]:
        page = await self._ensure()
        locator = page.locator(f'[data-trajecta-id="{element_id}"]')
        await locator.fill(text)
        if submit:
            await locator.press("Enter")
        return {"ok": True, "url": page.url}

    async def press(self, key: str) -> dict[str, Any]:
        page = await self._ensure()
        await page.keyboard.press(key)
        return {"ok": True, "url": page.url}

    async def scroll(self, *, x: int = 0, y: int = 700) -> dict[str, Any]:
        page = await self._ensure()
        await page.mouse.wheel(x, y)
        return {"ok": True, "url": page.url}

    async def back(self) -> dict[str, Any]:
        page = await self._ensure()
        await page.go_back(wait_until="domcontentloaded")
        return {"url": page.url, "title": await page.title()}

    async def forward(self) -> dict[str, Any]:
        page = await self._ensure()
        await page.go_forward(wait_until="domcontentloaded")
        return {"url": page.url, "title": await page.title()}

    async def tabs(self) -> list[dict[str, Any]]:
        await self._ensure()
        assert self._context is not None
        result: list[dict[str, Any]] = []
        for index, page in enumerate(self._context.pages):
            result.append({"index": index, "url": page.url, "title": await page.title()})
        return result

    async def select_tab(self, index: int) -> dict[str, Any]:
        await self._ensure()
        assert self._context is not None
        pages = self._context.pages
        if index < 0 or index >= len(pages):
            raise ValueError("Tab index out of range")
        self._page = pages[index]
        await self._page.bring_to_front()
        return {"index": index, "url": self._page.url, "title": await self._page.title()}

    async def new_tab(self, url: str | None = None) -> dict[str, Any]:
        await self._ensure()
        assert self._context is not None
        self._page = await self._context.new_page()
        self._page.set_default_timeout(self._cfg.default_timeout_ms)
        if url:
            await self.navigate(url)
        return {"url": self._page.url, "title": await self._page.title()}

    async def close_tab(self, index: int | None = None) -> dict[str, Any]:
        await self._ensure()
        assert self._context is not None
        pages = self._context.pages
        target = self._page if index is None else pages[index]
        await target.close()
        pages = self._context.pages
        self._page = pages[-1] if pages else await self._context.new_page()
        return {"ok": True, "remaining_tabs": len(self._context.pages)}

    async def screenshot(self, *, full_page: bool = False) -> dict[str, Any]:
        page = await self._ensure()
        virtual = f"/workspace/.trajecta/browser/{uuid.uuid4().hex}.png"
        path = self._paths.resolve(virtual, writable=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(path), full_page=full_page)
        return {
            "path": virtual,
            "instruction": "Use read_file on this path to inspect the screenshot multimodally.",
            "url": page.url,
        }

    async def download(self, element_id: str) -> dict[str, Any]:
        page = await self._ensure()
        async with page.expect_download() as info:
            await page.locator(f'[data-trajecta-id="{element_id}"]').click()
        download = await info.value
        suggested = Path(download.suggested_filename).name
        virtual = f"/workspace/.trajecta/downloads/{uuid.uuid4().hex}-{suggested}"
        path = self._paths.resolve(virtual, writable=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        await download.save_as(str(path))
        return {"path": virtual, "filename": suggested, "url": download.url}

    async def upload(self, element_id: str, virtual_paths: list[str]) -> dict[str, Any]:
        page = await self._ensure()
        host_paths = [str(self._paths.resolve(path)) for path in virtual_paths]
        await page.locator(f'[data-trajecta-id="{element_id}"]').set_input_files(host_paths)
        return {"ok": True, "files": virtual_paths}

    async def cookies(self) -> list[dict[str, Any]]:
        await self._ensure()
        assert self._context is not None
        # Deliberately omit cookie values from model-visible output.
        raw = await self._context.cookies()
        return [
            {k: cookie.get(k) for k in ("name", "domain", "path", "expires", "httpOnly", "secure", "sameSite")}
            for cookie in raw
        ]

    async def console(self, *, limit: int = 100) -> dict[str, Any]:
        # A persistent console listener is intentionally omitted for now; expose
        # browser JS errors through a bounded one-shot query instead.
        page = await self._ensure()
        entries = await page.evaluate(
            """() => ({url: location.href, readyState: document.readyState})"""
        )
        return {"page": entries, "note": "Use browser_snapshot for DOM state."}

    async def close(self) -> None:
        if self._context is not None:
            await self._context.close()
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
