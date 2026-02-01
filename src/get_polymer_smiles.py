import asyncio
import base64
import json
import pathlib
import re
import sys
from datetime import datetime
from playwright.async_api import async_playwright



API_URL = "https://polymer.nims.go.jp/PoLyInfo/api/6627766"

# Use the actual page you land on
PAGE_URL = "https://polymer.nims.go.jp/PoLyInfo/search"
REFERER = PAGE_URL

OUTDIR = pathlib.Path("data", "polyinfo_json")
OUTDIR.mkdir(exist_ok=True)

INDEX_PATH = OUTDIR / "index.jsonl"
ERRORS_PATH = OUTDIR / "errors.tsv"

SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")



def safe_filename(stem: str) -> str:
    stem = stem.strip()
    stem = SAFE_NAME_RE.sub("_", stem)
    return stem[:200] if stem else "unknown"

def decode_b64_json(b64: str) -> dict:
    raw = base64.b64decode(b64).decode("utf-8")
    return json.loads(raw)

def atomic_write_json(path: pathlib.Path, obj: dict):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2))
    tmp.replace(path)

def looks_blocked(status: int, content_type: str) -> bool:
    if status != 200:
        return True
    if "json" not in (content_type or "").lower():
        return True
    return False

def append_index(pid_uuid: str, decoded: dict, filename: str):
    rec = {
        "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "pid_uuid": pid_uuid,
        "polymer_id": decoded.get("polymer_id"),
        "polymer_uuid": decoded.get("polymer_uuid"),
        "filename": filename,
    }
    with INDEX_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

async def main(pids):
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir="pw_profile",
            headless=False,
        )
        page = await ctx.new_page()

        # ---- manual login ----
        await page.goto(PAGE_URL)
        input(
            "\nLogin manually in the opened Chromium window.\n"
            "If you see a captcha, solve it.\n"
            "Stay on the search page.\n"
            "Press Enter here when ready...\n"
        )

        # Capture captchaId whenever the UI makes the API call
        captcha_id = None

        async def on_request(req):
            nonlocal captcha_id
            if req.url == API_URL and req.method == "POST":
                h = req.headers  # lowercased keys
                if "captchaid" in h:
                    captcha_id = h["captchaid"]

        page.on("request", on_request)

        async def ensure_captcha_id():
            nonlocal captcha_id
            if captcha_id:
                return True
            print(
                "\nNeed captchaId.\n"
                "In the browser, click ONE polymer so its details load (this triggers the request once).\n"
                "Then press Enter here.\n"
            )
            input()
            return bool(captcha_id)

        def make_headers():
            return {
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": "https://polymer.nims.go.jp",
                "Referer": REFERER,
                "X-Requested-With": "XMLHttpRequest",
                "captchaId": captcha_id,
            }

        print(
            "\nStepper mode:\n"
            "  Enter = fetch current pid_uuid\n"
            "  r     = (re)learn captchaId (click one polymer in UI, then Enter)\n"
            "  s     = skip this pid_uuid\n"
            "  q     = quit\n"
        )

        ERRORS_PATH.write_text("")  # reset each run

        idx = 0
        while idx < len(pids):
            pid_uuid = pids[idx]

            # pointer file for resumability
            pid_uuid_path = OUTDIR / f"{safe_filename(pid_uuid)}.pointer.json"
            if pid_uuid_path.exists():
                idx += 1
                continue

            if not await ensure_captcha_id():
                print("Could not capture captchaId. Aborting.")
                break

            cmd = input(f"[{idx+1}/{len(pids)}] pid_uuid={pid_uuid} > ").strip().lower()
            if cmd == "q":
                break
            if cmd == "s":
                idx += 1
                continue
            if cmd == "r":
                captcha_id = None
                print("Click ONE polymer in the UI to trigger a details load, then press Enter here.")
                input()
                if not captcha_id:
                    print("captchaId still not observed. Try clicking again.")
                else:
                    print(f"Updated captchaId: {captcha_id}")
                continue  # do not advance

            try:
                resp = await page.request.post(API_URL, headers=make_headers(), form={"pid_uuid": pid_uuid})
                ct = resp.headers.get("content-type", "")

                if looks_blocked(resp.status, ct):
                    body_head = (await resp.text())[:400]
                    print("\nSTOP (possible captcha/block):")
                    print(f"HTTP {resp.status}, content-type={ct!r}")
                    print(f"Body head: {body_head!r}")
                    print("Solve captcha / re-login in browser if needed, then type 'r' to relearn captchaId and retry.")
                    continue  # retry same pid_uuid

                top = await resp.json()
                if not top.get("result", False):
                    print("\nSTOP: API returned result=false.")
                    print("message:", top.get("message"))
                    print("Solve captcha / re-login in browser if needed, then type 'r' and retry.")
                    continue

                decoded = decode_b64_json(top["json"])

                polymer_id = decoded.get("polymer_id")
                if polymer_id and isinstance(polymer_id, str):
                    fname = safe_filename(polymer_id) + ".json"
                else:
                    fname = safe_filename(pid_uuid) + ".json"

                out_path = OUTDIR / fname
                if out_path.exists():
                    out_path = OUTDIR / (safe_filename(polymer_id or "unknown") + "__" + safe_filename(pid_uuid) + ".json")

                atomic_write_json(out_path, decoded)
                append_index(pid_uuid, decoded, out_path.name)

                # pointer file marks completion for this pid_uuid
                atomic_write_json(pid_uuid_path, {"pid_uuid": pid_uuid, "filename": out_path.name})

                print(f"Saved {out_path.name}")
                idx += 1

            except Exception as e:
                with ERRORS_PATH.open("a", encoding="utf-8") as f:
                    f.write(f"{pid_uuid}\t{e}\n")
                print("\nSTOP: exception while fetching/decoding/saving:", e)
                print("Check browser for captcha/login changes, then retry.")
                continue

        await ctx.close()

if __name__ == "__main__":
    pids = [x.strip() for x in open("./data/pid_uuids.txt", encoding="utf-8") if x.strip()]
    asyncio.run(main(pids))
