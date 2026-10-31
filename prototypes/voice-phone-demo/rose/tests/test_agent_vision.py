"""The phone agent with vision, offline: the real model and a fake Rose Agent serving captured screens.

    ROSE_SHOTS=/path/to/shots ROSE_GPU_LOCK=/path/to/gpu.lock \\
        .venv/bin/python -m rose.tests.test_agent_vision [--only code,projects,hidden,fake] [--cache]

``ROSE_SHOTS`` is the folder of private Fold cover screenshots (``com.anthropic.claude.png`` and
its uiautomator dump ``com.anthropic.claude.xml``); they are only read, never written or printed.
``ShotLink`` plays Rose Agent: the dump becomes ``inspect_ui`` JSON nodes (the walk and
``clickTarget`` rules of PhoneAccessibility.java), ``screenshot`` returns the PNG scaled to 1600 px
as a JPEG with the real display size, and ``tap``/``tap_label``/``touch_point`` follow Rose Agent's
checks (snapshot id, clickable target, point inside the node). Tapping a menu row opens a drawn
page for that row. The process holds ``ROSE_GPU_LOCK`` while the model is loaded.

Scenarios (RealPhone, vision on):
  code      "Open Code in the Claude menu": a tap on the Code row, then a finish seen on the Code page.
  projects  "Open Projects": the same for the Projects row.
  hidden    "Open Code in the Claude menu" with the Code row removed from the tree (still in the
            screenshot): it must be reached with tap_point (Rose Agent's touch_point).
  fake      the fake phone with vision switched on: "Turn on Do Not Disturb".
``--cache`` measures what keeping earlier step screenshots in the agent cache costs against
dropping them (the numbers are recorded in rose/tasks.py). Exit code 0 only if every scenario passed.
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import io
import os
import statistics
import sys
import threading
import time
import xml.etree.ElementTree as ET

os.environ.setdefault("HF_HUB_OFFLINE", "1")

from PIL import Image, ImageDraw, ImageFont

from rose.brain import GEN_FAST, GREEDY, IM_END, Brain, tool_segment
from rose.bus import Bus
from rose.phone import Phone
from rose.realphone import RealPhone
from rose.runtime import BACKGROUND, Runtime
from rose.tasks import BG_CHUNK, CALL_PREFIX, Task, TaskManager
from rose.tests.test_vision import gpu_lock

PKG = "com.anthropic.claude"
MENU_ROWS = ("Projects", "Code", "Artifacts", "Scheduled", "Dispatch")


# ---------------------------------------------------------------- the fake Rose Agent
def _bounds(raw: str) -> list[int]:
    a, b = raw.strip("[]").split("][")
    return [int(v) for v in a.split(",") + b.split(",")]


def uiautomator_nodes(path: str) -> list[dict]:
    """A uiautomator dump as Rose Agent's inspect_ui nodes (pre-order ids, as PhoneAccessibility.walk)."""

    nodes: list[dict] = []

    def walk(el: ET.Element, parent: int) -> None:
        for child in el.findall("node"):
            b = _bounds(child.get("bounds", "[0,0][0,0]"))
            if b[2] <= b[0] or b[3] <= b[1]:
                continue
            secret = child.get("password") == "true"
            cls = child.get("class", "")
            node = {"id": len(nodes), "parentId": parent, "class": cls, "viewId": child.get("resource-id", ""),
                    "text": "[password]" if secret else child.get("text", ""), "visible": True,
                    "description": "" if secret else child.get("content-desc", ""), "password": secret,
                    "clickable": child.get("clickable") == "true",
                    "longClickable": child.get("long-clickable") == "true",
                    "editable": "EditText" in cls, "scrollable": child.get("scrollable") == "true",
                    "focused": child.get("focused") == "true", "imeEnter": False,
                    "enabled": child.get("enabled") == "true", "checked": child.get("checked") == "true",
                    "selected": child.get("selected") == "true", "bounds": b}
            nodes.append(node)
            walk(child, node["id"])

    walk(ET.parse(path).getroot(), -1)
    return with_targets(nodes)


def with_targets(nodes: list[dict]) -> list[dict]:
    """clickTarget as LabelTargets.target: the nearest enabled clickable ancestor-or-self."""

    by_id = {n["id"]: n for n in nodes}
    for n in nodes:
        n.pop("clickTarget", None)
        cur = n
        for _ in range(51):
            if cur is None or not cur["enabled"] or cur["password"]:
                break
            if cur["clickable"]:
                n["clickTarget"] = cur["id"]
                break
            cur = by_id.get(cur["parentId"])
    return nodes


def _font(size: int):
    try:
        return ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size)
    except OSError:
        return ImageFont.load_default(size)


def drawn_page(title: str, size: tuple[int, int]) -> tuple[Image.Image, list[dict]]:
    """The page a menu row opens: a title bar with a back arrow, and two lines of content."""

    w, h = size
    img = Image.new("RGB", size, "#262624")
    d = ImageDraw.Draw(img)
    d.text((60, 190), "<", font=_font(72), fill="#f0eee6")
    d.text((170, 190), title, font=_font(72), fill="#f0eee6")
    lines = [f"Your {title.lower()}", f"New {title.lower().rstrip('s')}"]
    for i, line in enumerate(lines):
        d.text((80, 420 + i * 160), line, font=_font(52), fill="#c2c0b6")
    nodes = [{"id": 0, "parentId": -1, "class": "android.widget.FrameLayout", "bounds": [0, 0, w, h]},
             {"id": 1, "parentId": 0, "class": "android.view.View", "description": "Navigate up",
              "clickable": True, "bounds": [30, 160, 150, 300]},
             {"id": 2, "parentId": 0, "class": "android.widget.TextView", "text": title,
              "bounds": [170, 190, 900, 280]}]
    for i, line in enumerate(lines):
        nodes.append({"id": 3 + 2 * i, "parentId": 0, "class": "android.view.View", "clickable": True,
                      "bounds": [40, 390 + i * 160, w - 40, 530 + i * 160]})
        nodes.append({"id": 4 + 2 * i, "parentId": 3 + 2 * i, "class": "android.widget.TextView", "text": line,
                      "bounds": [80, 420 + i * 160, 900, 490 + i * 160]})
    base = {"viewId": "", "text": "", "description": "", "visible": True, "password": False, "clickable": False,
            "longClickable": False, "editable": False, "scrollable": False, "focused": False, "imeEnter": False,
            "enabled": True, "checked": False, "selected": False}
    return img, with_targets([{**base, **n} for n in nodes])


class ShotLink:
    """Rose Agent for RealPhone, serving the captured Claude menu and the pages its rows open."""

    connected, model = True, "SM-F971U1 (captured screens)"

    def __init__(self, folder: str, hide: tuple[str, ...] = ()):
        with Image.open(os.path.join(folder, f"{PKG}.png")) as im:
            menu = im.convert("RGB")
        nodes = uiautomator_nodes(os.path.join(folder, f"{PKG}.xml"))
        by_id = {n["id"]: n for n in nodes}
        # Each menu row is the clickable ancestor of its label (the real hit areas, hidden or not).
        self.rows = {}
        for n in nodes:
            if n["text"] in MENU_ROWS and n.get("clickTarget") is not None:
                self.rows[n["text"]] = by_id[n["clickTarget"]]["bounds"]
        drop = set()
        for n in nodes:
            if n["text"] in hide:  # the label and its row, as if the app exposed neither
                drop |= {n["id"], n.get("clickTarget", n["id"])}
        nodes = [n for n in nodes if n["id"] not in drop and n["parentId"] not in drop]
        self.size = menu.size
        self.screens = {"menu": (menu, nodes)}
        self.stack = ["menu"]
        self.calls: list[tuple[str, dict]] = []
        self.opened: list[tuple[str, str]] = []  # (row, how) every time a row was opened
        self.snapshot = None
        self._n = 0
        self._lock = threading.Lock()

    @property
    def here(self) -> str:
        return self.stack[-1]

    def _page(self, name: str) -> tuple[Image.Image, list[dict]]:
        if name not in self.screens:
            self.screens[name] = drawn_page(name, self.size)
        return self.screens[name]

    def call(self, action: str, timeout: float = 25.0, **args) -> dict:
        with self._lock:
            self.calls.append((action, dict(args)))
            return self._call(action, args)

    def _call(self, action: str, a: dict) -> dict:
        image, nodes = self._page(self.here)
        by_id = {n["id"]: n for n in nodes}
        if action == "list_apps":
            return {"status": "ok", "apps": [{"label": "Claude", "packageName": PKG}]}
        if action == "inspect_ui":
            self._n += 1
            self.snapshot = f"s{self._n}"
            return {"status": "ok", "packageName": PKG, "windowId": 11, "nodes": [dict(n) for n in nodes],
                    "truncated": False, "snapshotId": self.snapshot, "validForMs": 30000}
        if action == "screenshot":
            scale = min(1.0, 1600 / max(image.size))
            shot = image.resize((round(image.width * scale), round(image.height * scale)), Image.BILINEAR)
            buf = io.BytesIO()
            shot.save(buf, format="JPEG", quality=75)
            return {"status": "ok", "screenshotId": "f", "width": shot.width, "height": shot.height,
                    "displayWidth": image.width, "displayHeight": image.height, "mimeType": "image/jpeg",
                    "imageBase64": base64.b64encode(buf.getvalue()).decode(), "packageName": PKG}
        if action == "open_app":
            return {"status": "opened" if a.get("packageName") == PKG else "not_found"}
        if action == "back":
            if len(self.stack) > 1:
                self.stack.pop()
            self.snapshot = None
            return {"status": "action_accepted"}
        if action == "home":
            self.stack.append("Home screen")
            self.snapshot = None
            return {"status": "action_accepted"}
        if action not in ("tap", "tap_label", "touch_point"):
            return {"status": "error", "message": f"unsupported {action}"}
        if self.snapshot is None or a.get("snapshotId") != self.snapshot:
            return {"status": "stale_screen", "message": "Inspect the current screen again before acting."}
        node = by_id.get(a.get("nodeId"))
        if action == "tap_label":
            targets = {n["clickTarget"] for n in nodes if a.get("label") in (n["text"], n["description"])
                       and n.get("clickTarget") is not None}
            if a.get("nodeId") is not None:
                if a["nodeId"] not in targets:
                    return {"status": "label_target_mismatch"}
                targets = {a["nodeId"]}
            if len(targets) != 1:
                return {"status": "label_not_found" if not targets else "ambiguous_label"}
            node = by_id[targets.pop()]
        if node is None or not node["enabled"]:
            return {"status": "node_unavailable"}
        b = node["bounds"]
        if action == "touch_point":
            x, y = a.get("x"), a.get("y")
            if not (b[0] <= x < b[2] and b[1] <= y < b[3]):  # TapPoint.checked
                return {"status": "error", "message": "Tap must stay inside the visible target"}
            self._hit(x, y, "touch_point")
            self.snapshot = None
            return {"status": "gesture_dispatched", "gestureId": "g"}
        if not node["clickable"]:
            return {"status": "action_failed"}
        self._hit((b[0] + b[2]) // 2, (b[1] + b[3]) // 2, action)
        self.snapshot = None
        return {"status": "action_accepted"}

    def _hit(self, x: int, y: int, how: str) -> None:
        """What a touch at display (x, y) does: a menu row opens its page; on a page, the arrow goes back."""

        if self.here == "menu":
            for row, b in self.rows.items():
                if b[0] <= x < b[2] and b[1] <= y < b[3]:
                    self.stack.append(row)
                    self.opened.append((row, how))
                    return
        elif 30 <= x < 150 and 160 <= y < 300:
            self.stack.pop()


# ------------------------------------------------------------------- harness
class Harness:
    def __init__(self):
        self.bus = Bus()
        self.rt = Runtime()
        t = time.monotonic()
        self.brain = self.rt.run(lambda: Brain(), timeout=600)
        print(f"brain loaded in {time.monotonic() - t:.1f} s", flush=True)
        self.cond = threading.Condition()
        self.done: set[int] = set()
        self.failures: list[str] = []
        self.rows: list[str] = []

    def manager(self, phone) -> TaskManager:
        tm = TaskManager(self.rt, self.brain, phone, self.bus, self._on_done)
        t = time.monotonic()
        tm.prepare()
        print(f"agent prompt prefilled in {time.monotonic() - t:.1f} s ({len(tm._agent.tokens)} tokens)", flush=True)
        return tm

    def _on_done(self, task: Task) -> None:
        with self.cond:
            self.done.add(id(task))  # task ids restart with every TaskManager
            self.cond.notify_all()

    def run(self, tm: TaskManager, goal: str, timeout: float = 240.0) -> Task:
        task = tm.start_phone(goal)
        with self.cond:
            self.cond.wait_for(lambda: id(task) in self.done, timeout)
        print(f"  goal: {goal!r} -> {task.status}: {task.result}")
        for s in task.steps:
            img = s.get("image") or {}
            print(f"    step {s['n']}: {s['ms']} ms (prefill {s['prefill_ms']}, gpu {s['gpu_ms']}), "
                  f"{s['tokens']} decoded + {s['forced']} forced"
                  + (f", image {img['tokens']} tok {img['ms']} ms max unit {img['max_unit_ms']} ms" if img else "")
                  + f": {s['what']}" + (f" [{s['stop']}]" if s.get("stop") else ""))
            for act in s["actions"]:
                if act["name"] == "tap_point" or not act["ok"]:
                    print(f"      {act['name']} {act['args']} -> {act['result']}")
        return task

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        print(f"  {'PASS' if ok else 'FAIL'} {name}{': ' + detail if detail else ''}", flush=True)
        if not ok:
            self.failures.append(f"{name}: {detail}")

    def report(self, name: str, task: Task, ok: bool) -> None:
        ms = [s["ms"] for s in task.steps]
        self.rows.append(f"{name:9s} {'ok  ' if ok else 'FAIL'} steps {len(task.steps)}  "
                         f"ms/step mean {statistics.mean(ms) if ms else 0:.0f} max {max(ms) if ms else 0}  "
                         f"total {task.to_dict()['ms']} ms")


def real_scenario(h: Harness, tm: TaskManager, phone: RealPhone, name: str, goal: str, row: str,
                  hide: tuple[str, ...], how: set[str]) -> None:
    link = ShotLink(os.environ["ROSE_SHOTS"], hide)
    phone.link, phone._apps = link, []
    print(f"\n[{name}]", flush=True)
    task = h.run(tm, goal)
    hit = link.opened[:1]
    ok_tap = bool(hit) and hit[0][0] == row and hit[0][1] in how
    h.check(f"{name}: first page opened is {row} by {'/'.join(sorted(how))}", ok_tap, f"opened {link.opened}")
    seen = task.steps and len(task.steps) >= 2
    ok_done = task.status == "done" and link.here == row and bool(seen)
    h.check(f"{name}: finished with success on the {row} page it had seen", ok_done,
            f"{task.status} on {link.here!r} after {len(task.steps)} steps")
    if hide:
        hidden = [n for n in link.screens["menu"][1] if n["text"] in hide]
        h.check(f"{name}: {hide} absent from the tree", not hidden)
    h.report(name, task, ok_tap and ok_done)


def fake_scenario(h: Harness) -> None:
    print("\n[fake]", flush=True)
    phone = Phone(h.bus, vision=True)
    tm = h.manager(phone)
    task = h.run(tm, "Turn on Do Not Disturb")
    ok = task.status == "done" and phone.data["settings"]["Do Not Disturb"]
    h.check("fake: Do Not Disturb on, with screenshots", ok, f"{task.status}, DND {phone.data['settings']['Do Not Disturb']}")
    h.check("fake: steps carried a screenshot", bool(task.steps) and all(s.get("image") for s in task.steps))
    h.report("fake", task, ok)


# ----------------------------------------------------------------- cache cost
def cache_bench(h: Harness, tm: TaskManager, phone: RealPhone, steps: int = 6) -> None:
    """Keep earlier step screenshots, or drop them? Measured on the agent's own cache.

    Each simulated step appends what a real step does: a tool response with the screenshot and the
    menu tree, then a short reply (a tap call). For every step it reports the image append, the
    decode cost per token, and the full-attention KV memory; and what dropping the previous
    screenshot would cost instead: rewinding to before it and prefilling the text after it again.
    """

    print("\n[cache]", flush=True)
    link = ShotLink(os.environ["ROSE_SHOTS"])
    phone.link, phone._apps = link, []
    s = phone.session("bench")
    s.refresh()
    s.ensure_image()
    image, tree = s.look(), s.ui_tree()
    reply = ("=tap>\n<parameter=id>\n2\n</parameter>\n</function>\n</tool_call>" + IM_END + "\n")
    out: dict = {}

    def job():
        seq = tm._agent.fork("bench")
        brain = h.brain
        rows = []
        for k in range(1, steps + 1):
            head = tool_segment(["Tapped Code.\n\n\x00"]).split("\x00")[0]
            before = seq.mark()
            yield from seq.append_text(head, chunk=BG_CHUNK)
            t = time.perf_counter()
            yield from seq.append_image(image)
            img_ms = (time.perf_counter() - t) * 1e3
            text = "\n" + tree + IM_END + "\n" + GEN_FAST + CALL_PREFIX
            t = time.perf_counter()
            yield from seq.append_text(text, chunk=BG_CHUNK)
            text_ms = (time.perf_counter() - t) * 1e3
            m = seq.mark()
            t = time.perf_counter()
            n = 0
            for _ in seq.generate(dataclasses.replace(GREEDY, max_tokens=16), stop={-1}):
                n += 1
                yield None
            dec_ms = (time.perf_counter() - t) * 1e3 / n
            seq.rewind(m)
            yield from seq.append_text(reply, chunk=BG_CHUNK)
            # Dropping this step's screenshot later = rewind to `before`, re-prefill everything after it as text.
            redo = brain.encode(head + text + reply)
            fork = seq.fork("redo")
            fork.rewind(before)
            t = time.perf_counter()
            yield from fork.prefill(redo, chunk=BG_CHUNK)
            redo_ms = (time.perf_counter() - t) * 1e3
            kv = sum(c.keys[..., : c.offset, :].nbytes * 2 for c in seq.cache
                     if getattr(c, "keys", None) is not None and hasattr(c, "offset"))
            rows.append((k, len(seq.tokens), round(img_ms), round(text_ms), round(dec_ms, 2), len(redo),
                         round(redo_ms), round(kv / 2**20, 1), round(seq.last_image["max_unit_ms"], 1)))
        out["rows"] = rows

    h.rt.submit(job, priority=BACKGROUND, name="cache-bench").result(600)
    print("  step  ctx_tokens  image_ms  tree_ms  decode_ms/tok  redo_tokens  redo_ms  kv_MiB  image_max_unit_ms")
    for r in out["rows"]:
        print("  " + "  ".join(f"{v:>{w}}" for v, w in zip(r, (4, 10, 8, 7, 13, 11, 7, 6, 17))))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="code,projects,hidden,fake")
    ap.add_argument("--cache", action="store_true")
    args = ap.parse_args()
    if not os.environ.get("ROSE_SHOTS"):
        raise SystemExit("set ROSE_SHOTS to the folder of private screenshots")
    only = set(args.only.split(",")) if args.only else set()
    with gpu_lock():
        h = Harness()
        try:
            phone = RealPhone(h.bus)
            phone.link = ShotLink(os.environ["ROSE_SHOTS"])
            if only & {"code", "projects", "hidden"} or args.cache:
                tm = h.manager(phone)
            if "code" in only:
                real_scenario(h, tm, phone, "code", "Open Code in the Claude menu", "Code", (),
                              {"tap", "tap_label"})
            if "projects" in only:
                real_scenario(h, tm, phone, "projects", "Open Projects", "Projects", (), {"tap", "tap_label"})
            if "hidden" in only:
                real_scenario(h, tm, phone, "hidden", "Open Code in the Claude menu", "Code", ("Code",),
                              {"touch_point"})
            if "fake" in only:
                fake_scenario(h)
            if args.cache:
                cache_bench(h, tm, phone)
        finally:
            h.rt.stop()
    print("\nsummary:")
    for row in h.rows:
        print("  " + row)
    if h.failures:
        raise SystemExit("FAILED:\n  " + "\n  ".join(h.failures))
    print("agent vision checks passed")


if __name__ == "__main__":
    main()
