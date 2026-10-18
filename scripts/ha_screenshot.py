"""Screenshot of a real Home Assistant frontend at any screen size.

    AAC_HA_URL=http://ha.local AAC_HA_TOKEN_FILE=~/token \
      python scripts/ha_screenshot.py out.png 390 844 [/aac] [js-to-run-after-load]

Drives headless Firefox over WebDriver BiDi (only aiohttp needed, no
selenium/geckodriver) and logs in with a long-lived access token.
To reach the AAC panel from the JS argument, use FIND_PANEL, e.g.
    "(()=>{const p=FIND_PANEL; p._go('settings/master');})()"
"""

import asyncio
import base64
import json
import os
import pathlib
import subprocess
import sys
import tempfile

import aiohttp

HA = os.environ["AAC_HA_URL"].rstrip("/")
TOKEN = pathlib.Path(os.environ["AAC_HA_TOKEN_FILE"]).expanduser().read_text().strip()
PROF = pathlib.Path(tempfile.gettempdir()) / "aac-bidi-profile"
FIND_PANEL = (
    '(function f(r){for(const e of r.querySelectorAll("*")){if(e.tagName==="AAC-PANEL")return e;'
    "if(e.shadowRoot){const x=f(e.shadowRoot);if(x)return x}}})(document)"
)

async def main(out, w, h, path="/aac", js=None):
    PROF.mkdir(exist_ok=True)
    if js:
        js = js.replace("FIND_PANEL", FIND_PANEL)
    ff = subprocess.Popen(["firefox", "--headless", "--no-remote", "--profile", str(PROF), "--remote-debugging-port", "9223"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        async with aiohttp.ClientSession() as s:
            for _ in range(60):
                try:
                    ws = await s.ws_connect("ws://127.0.0.1:9223/session", max_msg_size=0); break
                except aiohttp.ClientError:
                    await asyncio.sleep(0.5)
            n = 0
            async def cmd(method, params):
                nonlocal n; n += 1; await ws.send_json({"id": n, "method": method, "params": params})
                while True:
                    r = await ws.receive_json(timeout=60)
                    if r.get("id") == n:
                        if r.get("type") == "error": raise RuntimeError(r)
                        return r["result"]
            await cmd("session.new", {"capabilities": {}})
            ctx = (await cmd("browsingContext.create", {"type": "tab"}))["context"]
            await cmd("browsingContext.setViewport", {"context": ctx, "viewport": {"width": w, "height": h}})
            await cmd("browsingContext.navigate", {"context": ctx, "url": HA + "/auth/authorize", "wait": "complete"})
            tokens = {"access_token": TOKEN, "token_type": "Bearer", "expires_in": 1800, "hassUrl": HA,
                      "clientId": HA + "/", "expires": 9999999999999, "refresh_token": ""}
            await cmd("script.evaluate", {"target": {"context": ctx}, "awaitPromise": False,
                      "expression": f"localStorage.setItem('hassTokens', {json.dumps(json.dumps(tokens))})"})
            await cmd("browsingContext.navigate", {"context": ctx, "url": HA + path, "wait": "complete"})
            await asyncio.sleep(6)
            if js:
                await cmd("script.evaluate", {"target": {"context": ctx}, "awaitPromise": True, "expression": js})
                await asyncio.sleep(1.5)
            shot = await cmd("browsingContext.captureScreenshot", {"context": ctx})
            pathlib.Path(out).write_bytes(base64.b64decode(shot["data"]))
            info = await cmd("script.evaluate", {"target": {"context": ctx}, "awaitPromise": False, "expression":
                "JSON.stringify({vw: innerWidth, vh: innerHeight, sw: document.documentElement.scrollWidth, sh: document.documentElement.scrollHeight})"})
            print(info["result"].get("value"))
    finally:
        ff.terminate()

a = sys.argv
asyncio.run(main(a[1], int(a[2]), int(a[3]), a[4] if len(a) > 4 else "/aac", a[5] if len(a) > 5 else None))
