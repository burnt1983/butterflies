#!/usr/bin/env python3
"""Eight coloured butterflies on the desktop.

They all fly at once. The choice bar stays hidden until a butterfly is
clicked, then it closes again shortly after the last choice. Point at a
butterfly and it turns away. It holds still for a moment as the pointer
arrives so the click can land. Scroll the bar to resize. Drag the bar to
move it. Right-click the bar for speed and login.

Only the butterfly itself takes the click. The rest of its window stays
click-through. Opens through XWayland so the bar can remember its place.
"""

from __future__ import annotations

import json
import math
import os
import random
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

HOME = Path.home()
SHARE = HOME / ".local" / "share" / "lee-insects"
SPRITES = SHARE / "sprites"
CFG = HOME / ".config" / "lee-insects" / "config.json"
PID = HOME / ".cache" / "lee-insects.pid"
LOG = Path("/tmp/insects-widget.log")
ICON = HOME / ".local" / "share" / "icons" / "hicolor" / "256x256" / "apps" / "lee-insects.png"
APP_DESKTOP = HOME / ".local" / "share" / "applications" / "lee-insects.desktop"
AUTOSTART = HOME / ".config" / "autostart" / "lee-insects.desktop"
PRGNAME = "lee-insects"

CELL = 62
GAP = 8
PAD = 14
COLS = 4
ROWS = 2
CAPTION = 40
PICKER_W = PAD * 2 + COLS * CELL + (COLS - 1) * GAP
PICKER_H = PAD + ROWS * CELL + (ROWS - 1) * GAP + CAPTION
FLEE_R = 158.0
SELECT_HIDE_MS = 1200
HOLD_SEC = 0.7
SPEEDS = (("Calm", 0.62), ("Natural", 1.0), ("Lively", 1.5))

_raise_picker = False


@dataclass(frozen=True)
class Species:
    id: str
    name: str
    speed: float
    length: float
    turn: float
    flap: float


SPECIES = (
    Species("monarch", "Monarch", 148, 250, 2.6, 8.0),
    Species("morpho", "Blue morpho", 132, 280, 2.2, 6.6),
    Species("swallowtail", "Swallowtail", 156, 270, 2.8, 7.8),
    Species("admiral", "Red admiral", 164, 240, 3.1, 8.6),
    Species("white", "Cabbage white", 142, 210, 3.0, 9.2),
    Species("birdwing", "Birdwing", 124, 290, 2.1, 6.2),
    Species("peacock", "Peacock", 150, 250, 2.7, 7.5),
    Species("zebra", "Zebra", 138, 260, 2.5, 8.8),
)
BY_ID = {item.id: item for item in SPECIES}


def log(message: str) -> None:
    try:
        with LOG.open("a", encoding="utf-8") as handle:
            handle.write(time.strftime("%H:%M:%S ") + message + "\n")
    except OSError:
        pass


def wrap(angle: float) -> float:
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


def steer(heading: float, desired: float, rate: float, dt: float) -> float:
    delta = wrap(desired - heading)
    step = rate * dt
    if delta > step:
        delta = step
    elif delta < -step:
        delta = -step
    return heading + delta


def mix_heading(a: float, b: float, weight: float) -> float:
    return a + wrap(b - a) * weight


def away_heading(x: float, y: float, mx: float, my: float) -> float:
    """Direction from the pointer to the butterfly. That is the way it flees."""
    return math.atan2(y - my, x - mx)


def edge_heading(x: float, y: float, bounds: tuple[int, int, int, int], margin: float):
    left, top, width, height = bounds
    vx = 0.0
    vy = 0.0
    if x < left + margin:
        vx += 1.0
    if x > left + width - margin:
        vx -= 1.0
    if y < top + margin:
        vy += 1.0
    if y > top + height - margin:
        vy -= 1.0
    if vx == 0.0 and vy == 0.0:
        return None
    return math.atan2(vy, vx)


def approach(value: float, target: float, rate: float, dt: float) -> float:
    step = rate * dt
    if abs(target - value) <= step:
        return target
    if target > value:
        return value + step
    return value - step


def face_left_for(heading: float, current: bool) -> bool:
    cosine = math.cos(heading)
    if cosine > 0.28:
        return False
    if cosine < -0.28:
        return True
    return current


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class Flyer:
    """One butterfly. Wings beat even while it glides."""

    def __init__(self) -> None:
        self.x = 0.0
        self.y = 0.0
        self.heading = -0.4
        self.wander = -0.4
        self.wander_t = 1.5
        self.flip = 1.0
        self.face_left = False
        self.flap = 0.0
        self.flutter = 0.0
        self.bank = 0.0
        self.t = 0.0
        self.hold_until = 0.0
        self.shadow_x = 0.0
        self.shadow_y = 0.0

    def place_start(self, bounds: tuple[int, int, int, int], index: int, count: int) -> None:
        left, top, width, height = bounds
        angle = (index / max(1, count)) * math.tau
        self.x = left + width * (0.50 + 0.30 * math.cos(angle))
        self.y = top + height * (0.44 + 0.22 * math.sin(angle))
        self.shadow_x = self.x
        self.shadow_y = self.y + 28.0
        self.heading = angle
        self.wander = self.heading
        self.flap = index * 0.85
        self.wander_t = 0.8 + (index % 5) * 0.35

    def update(
        self,
        dt: float,
        species: Species,
        scale: float,
        speed_mul: float,
        mx: float,
        my: float,
        bounds: tuple[int, int, int, int],
        neighbors: tuple["Flyer", ...] = (),
    ) -> None:
        self.t += dt
        self.flap += dt * species.flap
        if time.monotonic() < self.hold_until:
            self.flutter = math.sin(self.t * 2.0 + self.flap * 0.15) * 16.0 * scale
            return
        margin = species.length * scale * 0.42 + 20.0
        dist = math.hypot(self.x - mx, self.y - my)
        urgency = 0.0
        self.wander_t -= dt
        if self.wander_t <= 0.0:
            self.wander = random.uniform(-math.pi, math.pi)
            self.wander_t = random.uniform(1.3, 3.2)
        self.wander = wrap(self.wander + math.sin(self.t * 0.55) * dt * 0.35)
        desired = self.wander
        if dist < FLEE_R * 1.2:
            desired = away_heading(self.x, self.y, mx, my)
            urgency = 1.0 - dist / (FLEE_R * 1.2)
            self.wander = desired
        separate = 0.0
        ax = 0.0
        ay = 0.0
        for other in neighbors:
            dx = self.x - other.x
            dy = self.y - other.y
            gap = dx * dx + dy * dy
            if 1.0 < gap < 170.0 * 170.0:
                ax += dx / gap
                ay += dy / gap
                separate = 1.0
        if separate and urgency < 0.55:
            desired = mix_heading(desired, math.atan2(ay, ax), 0.34)
        edge = edge_heading(self.x, self.y, bounds, margin + 20.0)
        if edge is not None:
            desired = mix_heading(desired, edge, 0.2 if urgency > 0.7 else 0.6)
        rate = species.turn * (1.0 + urgency * 1.8)
        self.heading = steer(self.heading, desired, rate, dt)
        self.face_left = face_left_for(self.heading, self.face_left)
        self.flip = approach(self.flip, -1.0 if self.face_left else 1.0, 6.5, dt)
        self.bank = clamp(math.sin(self.heading) * 0.62, -0.7, 0.7)
        speed = species.speed * speed_mul * (1.0 + 1.4 * urgency)
        if abs(self.flip) < 0.3:
            speed *= 0.55
        self.x += math.cos(self.heading) * speed * dt
        self.y += math.sin(self.heading) * speed * dt
        self.flutter = math.sin(self.t * 2.0 + self.flap * 0.15) * 16.0 * scale
        left, top, width, height = bounds
        self.x = clamp(self.x, left + margin, left + width - margin)
        self.y = clamp(self.y, top + margin, top + height - margin)
        follow = min(1.0, dt * 3.2)
        self.shadow_x += (self.x - self.shadow_x) * follow
        self.shadow_y += (self.y + 26.0 * scale - self.shadow_y) * follow


def picker_should_hide(menu_open: bool, flying: int) -> bool:
    """Close the bar after a choice, unless no butterfly is left to click."""
    return (not menu_open) and flying > 0


def self_test() -> int:
    assert len(SPECIES) == 8
    assert len(BY_ID) == 8
    assert "ladybird" not in BY_ID
    assert "butterfly" not in BY_ID
    assert math.cos(away_heading(0, 0, 80, 0)) < -0.99
    assert math.sin(away_heading(0, 0, 0, -80)) > 0.99
    turned = steer(0.0, 1.0, 2.0, 0.05)
    assert abs(turned - 0.1) < 1e-9
    assert edge_heading(10, 100, (0, 0, 400, 300), 40) == 0.0
    assert PICKER_W == 300 and PICKER_H == 186
    monarch = BY_ID["monarch"]
    fly = Flyer()
    fly.x, fly.y, fly.heading = 0.0, 0.0, 0.0
    fly.shadow_x, fly.shadow_y = 0.0, 20.0
    bounds = (-2000, -2000, 4000, 4000)
    for _ in range(45):
        fly.update(1.0 / 30.0, monarch, 1.0, 1.0, 140.0, 0.0, bounds)
    if fly.x >= -12.0:
        print(f"butterfly flee failed, x={fly.x:.1f} heading={fly.heading:.2f}", file=sys.stderr)
        return 1
    missing = [item.id for item in SPECIES if not (SPRITES / f"{item.id}.png").is_file()]
    if missing:
        print("missing sprites: " + ", ".join(missing), file=sys.stderr)
        return 1
    hidden = _clean_hidden(["monarch", "ant", "monarch", "nope"])
    assert hidden == ["monarch"]
    held = Flyer()
    held.x, held.y = 10.0, 10.0
    held.hold_until = time.monotonic() + 2.0
    held.update(1.0 / 30.0, monarch, 1.0, 1.0, 0.0, 0.0, bounds)
    if held.x != 10.0 or held.y != 10.0:
        print(f"butterfly hold failed, x={held.x:.1f} y={held.y:.1f}", file=sys.stderr)
        return 1
    assert picker_should_hide(False, 3)
    assert not picker_should_hide(True, 3)
    assert not picker_should_hide(False, 0)
    print(f"butterflies self-test ok · 8 colours · flee x={fly.x:.0f}")
    return 0


def _clean_hidden(raw) -> list[str]:
    if not isinstance(raw, list):
        return []
    seen = []
    for item in raw:
        if item in BY_ID and item not in seen:
            seen.append(item)
    return seen


sys.path[:0] = [
    str(Path(__file__).resolve().parent),
    str(Path.home() / ".local" / "lib"),
]
from desklet_geometry import prefer_xwayland, sink_above_icons  # noqa: E402

prefer_xwayland()

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo  # noqa: E402

import cairo  # noqa: E402

CLEAR_STATES = (
    Gtk.StateFlags.NORMAL,
    Gtk.StateFlags.ACTIVE,
    Gtk.StateFlags.PRELIGHT,
    Gtk.StateFlags.SELECTED,
    Gtk.StateFlags.INSENSITIVE,
    Gtk.StateFlags.BACKDROP,
)
TRANS = Gdk.RGBA(0, 0, 0, 0)
GOLD = (0.90, 0.74, 0.32)


def paint_clear(widget: Gtk.Widget) -> None:
    context = widget.get_style_context()
    context.remove_class("background")
    for state in CLEAR_STATES:
        try:
            widget.override_background_color(state, TRANS)
        except Exception:
            pass


def apply_rgba(widget: Gtk.Widget) -> None:
    screen = widget.get_screen()
    if screen is None:
        return
    visual = screen.get_rgba_visual()
    if visual is not None and widget.get_visual() != visual:
        widget.set_visual(visual)


def screen_bounds() -> tuple[int, int, int, int]:
    display = Gdk.Display.get_default()
    if display is None or display.get_n_monitors() < 1:
        return 0, 0, 1920, 1080
    min_x = min_y = 10**9
    max_x = max_y = -10**9
    for index in range(display.get_n_monitors()):
        geo = display.get_monitor(index).get_geometry()
        min_x = min(min_x, int(geo.x))
        min_y = min(min_y, int(geo.y))
        max_x = max(max_x, int(geo.x + geo.width))
        max_y = max(max_y, int(geo.y + geo.height))
    return min_x, min_y, max_x - min_x, max_y - min_y


def work_area() -> tuple[int, int, int, int]:
    display = Gdk.Display.get_default()
    if display is None or display.get_n_monitors() < 1:
        return 0, 0, 1920, 1080
    monitor = display.get_primary_monitor() or display.get_monitor(0)
    if monitor is None:
        return screen_bounds()
    box = monitor.get_workarea()
    return int(box.x), int(box.y), int(box.width), int(box.height)


def pointer_xy() -> tuple[float, float] | None:
    display = Gdk.Display.get_default()
    if display is None:
        return None
    seat = display.get_default_seat()
    if seat is None:
        return None
    device = seat.get_pointer()
    if device is None:
        return None
    pos = device.get_position()
    if len(pos) >= 3:
        return float(pos[1]), float(pos[2])
    if len(pos) == 2:
        return float(pos[0]), float(pos[1])
    return None


def load_cfg() -> dict:
    cfg = {
        "x": None,
        "y": None,
        "scale": 1.0,
        "above": True,
        "speed": 1.0,
        "hidden": [],
    }
    try:
        raw = json.loads(CFG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    for key in ("x", "y", "scale", "above", "speed", "hidden"):
        if key in raw:
            cfg[key] = raw[key]
    try:
        cfg["scale"] = clamp(float(cfg.get("scale", 1.0)), 0.55, 2.0)
    except (TypeError, ValueError):
        cfg["scale"] = 1.0
    try:
        cfg["speed"] = float(cfg.get("speed", 1.0))
    except (TypeError, ValueError):
        cfg["speed"] = 1.0
    if cfg["speed"] not in {value for _label, value in SPEEDS}:
        cfg["speed"] = 1.0
    cfg["above"] = bool(cfg.get("above", True))
    cfg["hidden"] = _clean_hidden(cfg.get("hidden"))
    stale = any(key in raw for key in ("species", "butterfly"))
    if stale or raw.get("hidden") != cfg["hidden"]:
        save_cfg(cfg)
    return cfg


def save_cfg(cfg: dict) -> None:
    CFG.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "x": cfg.get("x"),
        "y": cfg.get("y"),
        "scale": cfg.get("scale", 1.0),
        "above": bool(cfg.get("above", True)),
        "speed": cfg.get("speed", 1.0),
        "hidden": list(cfg.get("hidden") or []),
    }
    tmp = CFG.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    tmp.replace(CFG)


def desktop_file() -> str:
    return "\n".join(
        (
            "[Desktop Entry]",
            "Version=1.0",
            "Type=Application",
            "Name=Butterflies",
            "Comment=Eight coloured butterflies. Click one to open the choice bar.",
            f"Exec={Path(__file__).resolve()}",
            f"Icon={ICON}",
            "Terminal=false",
            "Categories=Utility;GTK;",
            "StartupNotify=false",
            "StartupWMClass=lee-insects",
            "Keywords=butterflies;butterfly;desktop;widget;",
            "",
        )
    )


def _trust_launcher(path: Path) -> None:
    if path != APP_DESKTOP or not path.exists():
        return
    try:
        subprocess.run(
            ["gio", "set", str(path), "metadata::trusted", "true"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def install_launchers() -> None:
    body = desktop_file()
    for path in (APP_DESKTOP,):
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text(encoding="utf-8") != body:
            path.write_text(body, encoding="utf-8")
        _trust_launcher(path)
    kept = {}
    if AUTOSTART.exists():
        for line in AUTOSTART.read_text(encoding="utf-8").splitlines():
            if line.startswith("X-GNOME-Autostart-") or line.startswith("Hidden="):
                key = line.split("=", 1)[0]
                kept[key] = line
    else:
        kept["X-GNOME-Autostart-enabled"] = "X-GNOME-Autostart-enabled=false"
        kept["X-GNOME-Autostart-Delay"] = "X-GNOME-Autostart-Delay=5"
        kept["Hidden"] = "Hidden=true"
    lines = [line for line in body.splitlines() if line]
    for key in ("X-GNOME-Autostart-enabled", "X-GNOME-Autostart-Delay", "Hidden"):
        lines.append(kept.get(key, ""))
    text = "\n".join(line for line in lines if line) + "\n"
    AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
    if not AUTOSTART.exists() or AUTOSTART.read_text(encoding="utf-8") != text:
        AUTOSTART.write_text(text, encoding="utf-8")


def autostart_enabled() -> bool:
    if not AUTOSTART.exists():
        return False
    hidden = False
    enabled = False
    for line in AUTOSTART.read_text(encoding="utf-8").splitlines():
        if line.startswith("Hidden="):
            hidden = line.split("=", 1)[1].strip().lower() == "true"
        elif line.startswith("X-GNOME-Autostart-enabled="):
            enabled = line.split("=", 1)[1].strip().lower() == "true"
    return enabled and not hidden


def set_autostart(enabled: bool) -> None:
    install_launchers()
    lines = AUTOSTART.read_text(encoding="utf-8").splitlines()
    found_enabled = False
    found_hidden = False
    rewritten = []
    for line in lines:
        if line.startswith("X-GNOME-Autostart-enabled="):
            rewritten.append("X-GNOME-Autostart-enabled=" + ("true" if enabled else "false"))
            found_enabled = True
        elif line.startswith("Hidden="):
            rewritten.append("Hidden=" + ("false" if enabled else "true"))
            found_hidden = True
        else:
            rewritten.append(line)
    if not found_enabled:
        rewritten.append("X-GNOME-Autostart-enabled=" + ("true" if enabled else "false"))
    if not found_hidden:
        rewritten.append("Hidden=" + ("false" if enabled else "true"))
    AUTOSTART.write_text("\n".join(rewritten) + "\n", encoding="utf-8")


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        text = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
    except OSError:
        return False
    return "lee_insects_widget.py" in text or text.strip().endswith("/insects")


def claim_pid(replace: bool) -> None:
    PID.parent.mkdir(parents=True, exist_ok=True)
    if PID.exists():
        text = PID.read_text(encoding="utf-8").strip()
        if text.isdigit():
            old = int(text)
            if old != os.getpid() and pid_alive(old):
                if not replace:
                    os.kill(old, signal.SIGUSR1)
                    print("Butterflies are already on the desktop.", file=sys.stderr)
                    raise SystemExit(0)
                os.kill(old, signal.SIGTERM)
                for _ in range(30):
                    if not pid_alive(old):
                        break
                    time.sleep(0.05)
    PID.write_text(str(os.getpid()) + "\n", encoding="utf-8")


def release_pid() -> None:
    try:
        if PID.exists() and PID.read_text(encoding="utf-8").strip() == str(os.getpid()):
            PID.unlink()
    except OSError:
        pass


def write_icon() -> None:
    try:
        from PIL import Image, ImageDraw
    except Exception as exc:
        log(f"icon skipped {exc}")
        return
    sprite = SPRITES / "monarch.png"
    if not sprite.is_file():
        return
    side = 256
    icon = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon)
    draw.rounded_rectangle((8, 8, side - 8, side - 8), radius=48, fill=(11, 61, 61, 255))
    bug = Image.open(sprite).convert("RGBA")
    bug.thumbnail((188, 188), Image.Resampling.LANCZOS)
    icon.alpha_composite(bug, ((side - bug.width) // 2, (side - bug.height) // 2 + 4))
    ICON.parent.mkdir(parents=True, exist_ok=True)
    icon.save(ICON)


def load_surface(name: str) -> cairo.ImageSurface:
    path = SPRITES / f"{name}.png"
    if not path.is_file():
        raise FileNotFoundError(path)
    return cairo.ImageSurface.create_from_png(str(path))


def sprite_metrics(species: Species, surface: cairo.ImageSurface, scale: float):
    iw = surface.get_width()
    ih = surface.get_height()
    long_side = species.length * scale
    if iw >= ih:
        draw_w = long_side
        draw_h = long_side * ih / max(1, iw)
    else:
        draw_h = long_side
        draw_w = long_side * iw / max(1, ih)
    extra = 56.0 * scale
    win_w = int(draw_w + 64)
    win_h = int(draw_h + 100 + extra)
    return draw_w, draw_h, max(win_w, 80), max(win_h, 80), iw, ih


def rounded(cr: cairo.Context, x: float, y: float, w: float, h: float, r: float) -> None:
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def soft_ellipse(cr: cairo.Context, x: float, y: float, rx: float, ry: float, alpha: float) -> None:
    for grow, factor in ((1.0, 1.0), (1.45, 0.42), (2.0, 0.16)):
        cr.save()
        cr.translate(x, y)
        cr.scale(max(1.0, rx * grow), max(1.0, ry * grow))
        cr.arc(0, 0, 1, 0, math.tau)
        cr.set_source_rgba(0, 0, 0, alpha * factor)
        cr.fill()
        cr.restore()


def paint_sprite(
    cr: cairo.Context,
    surface: cairo.ImageSurface,
    cx: float,
    cy: float,
    draw_w: float,
    draw_h: float,
    iw: int,
    ih: int,
    flip: float,
    tilt: float,
    stretch: float,
) -> None:
    cr.save()
    cr.translate(cx, cy)
    cr.rotate(tilt)
    cr.scale((draw_w / iw) * flip, (draw_h / ih) * stretch)
    cr.translate(-iw / 2, -ih / 2)
    pattern = cairo.SurfacePattern(surface)
    pattern.set_filter(cairo.FILTER_BILINEAR)
    cr.set_source(pattern)
    cr.paint()
    cr.restore()


def cell_center(index: int) -> tuple[float, float]:
    col = index % COLS
    row = index // COLS
    cx = PAD + col * (CELL + GAP) + CELL / 2
    cy = PAD + row * (CELL + GAP) + CELL / 2
    return cx, cy


def _font(size_px: float) -> Pango.FontDescription:
    desc = Pango.FontDescription()
    desc.set_family("Cantarell")
    desc.set_absolute_size(max(8.0, size_px) * Pango.SCALE)
    desc.set_weight(Pango.Weight.MEDIUM)
    return desc


class SpriteWindow(Gtk.Window):
    """A clear window that follows one butterfly. Only the insect takes a click."""

    def __init__(self, title: str, paint, on_click) -> None:
        super().__init__(title=title)
        self.paint = paint
        self.on_click = on_click
        self._screening = False
        self._hit_key = None
        self.set_name(PRGNAME)
        self.set_role(title.lower())
        self.set_app_paintable(True)
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        self.set_type_hint(Gdk.WindowTypeHint.NORMAL)
        paint_clear(self)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect("screen-changed", self._on_screen)
        self.connect("draw", self._draw)
        self.connect("button-press-event", self._press)
        self.connect("map-event", self._on_map)
        self._on_screen()
        self._install_css()

    def _install_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(
            b"""
            window, window.background {
              background-color: transparent;
              background-image: none;
              border: none;
              box-shadow: none;
            }
            """
        )
        Gtk.StyleContext.add_provider_for_screen(
            self.get_screen(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _on_screen(self, *_args) -> None:
        if self._screening:
            return
        self._screening = True
        try:
            apply_rgba(self)
        finally:
            self._screening = False

    def _on_map(self, *_args) -> bool:
        self._hit_key = None
        gdkwin = self.get_window()
        if gdkwin is not None:
            try:
                gdkwin.set_opaque_region(cairo.Region())
            except Exception:
                pass
        return False

    def _press(self, _widget, event) -> bool:
        if int(event.button) != 1:
            return False
        self.on_click()
        return True

    def set_hit(self, rx: float, ry: float, cx: float, cy: float, width: int, height: int) -> None:
        """Keep clicks on the wings. The clear padding stays click-through."""
        key = (int(rx), int(ry), int(cx), int(cy), int(width), int(height))
        if key == self._hit_key:
            return
        gdkwin = self.get_window()
        if gdkwin is None or width < 2 or height < 2 or rx < 4 or ry < 4:
            return
        self._hit_key = key
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, int(width), int(height))
        cr = cairo.Context(surface)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_source_rgba(0, 0, 0, 1)
        cr.save()
        cr.translate(cx, cy)
        cr.scale(rx, ry)
        cr.arc(0, 0, 1, 0, math.tau)
        cr.restore()
        cr.fill()
        region = Gdk.cairo_region_create_from_surface(surface)
        try:
            gdkwin.input_shape_combine_region(region, 0, 0)
        except Exception as exc:
            log(f"hit shape {exc}")

    def _draw(self, _widget, cr: cairo.Context) -> bool:
        width = max(1, self.get_allocated_width())
        height = max(1, self.get_allocated_height())
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        self.paint(cr, width, height)
        return True


class Picker(Gtk.Window):
    def __init__(self, app: "App") -> None:
        super().__init__(title="Butterflies")
        self.app = app
        self._screening = False
        self._drag = None
        self._hover = None
        self._menu = None
        self._menu_lock = False
        self._hide_id = None
        self.set_name(PRGNAME)
        self.set_role("butterflies")
        self.set_app_paintable(True)
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(True)
        self.set_focus_on_map(False)
        self.set_type_hint(Gdk.WindowTypeHint.NORMAL)
        try:
            self.stick()
        except Exception:
            pass
        paint_clear(self)
        self.connect("screen-changed", self._on_screen)
        self.connect("delete-event", lambda *_: self.app.quit())
        self._on_screen()

        self.area = Gtk.DrawingArea()
        self.area.set_app_paintable(True)
        self.area.set_size_request(PICKER_W, PICKER_H)
        paint_clear(self.area)
        self.area.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.BUTTON1_MOTION_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
            | Gdk.EventMask.SCROLL_MASK
            | Gdk.EventMask.SMOOTH_SCROLL_MASK
        )
        self.area.connect("draw", self._draw)
        self.area.connect("button-press-event", self._press)
        self.area.connect("button-release-event", self._release)
        self.area.connect("motion-notify-event", self._motion)
        self.area.connect("leave-notify-event", self._leave)
        self.area.connect("scroll-event", self._scroll)
        self.add(self.area)
        self.set_default_size(PICKER_W, PICKER_H)
        provider = Gtk.CssProvider()
        provider.load_from_data(
            b"""
            window, window.background, #lee-insects, drawingarea {
              background-color: transparent;
              background-image: none;
              border: none;
              box-shadow: none;
            }
            """
        )
        Gtk.StyleContext.add_provider_for_screen(
            self.get_screen(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        try:
            self.set_icon_from_file(str(ICON))
        except Exception:
            pass

    def _on_screen(self, *_args) -> None:
        if self._screening:
            return
        self._screening = True
        try:
            apply_rgba(self)
            if getattr(self, "area", None) is not None:
                apply_rgba(self.area)
        finally:
            self._screening = False

    def hit(self, x: float, y: float):
        for index, species in enumerate(SPECIES):
            cx, cy = cell_center(index)
            if math.hypot(x - cx, y - cy) <= CELL * 0.48:
                return species.id
        return None

    def _flying(self, species_id: str) -> bool:
        return species_id not in self.app.cfg["hidden"]

    def _draw(self, _widget, cr: cairo.Context) -> bool:
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        rounded(cr, 0.5, 0.5, PICKER_W - 1, PICKER_H - 1, 22)
        cr.set_source_rgba(0.05, 0.07, 0.08, 0.78)
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.16)
        cr.set_line_width(1)
        cr.stroke()
        for index, species in enumerate(SPECIES):
            cx, cy = cell_center(index)
            radius = CELL / 2 - 4
            flying = self._flying(species.id)
            if flying:
                cr.set_source_rgba(1, 1, 1, 0.08)
                cr.arc(cx, cy, radius, 0, math.tau)
                cr.fill()
            self._thumb(cr, species.id, cx, cy, radius * 1.7, flying)
            cr.arc(cx, cy, radius, 0, math.tau)
            if flying:
                cr.set_source_rgba(*GOLD, 1)
                cr.set_line_width(2.6)
            elif self._hover == species.id:
                cr.set_source_rgba(1, 1, 1, 0.45)
                cr.set_line_width(1.5)
            else:
                cr.set_source_rgba(1, 1, 1, 0.12)
                cr.set_line_width(1.2)
            cr.stroke()
        if self._hover in BY_ID:
            label = BY_ID[self._hover].name
        else:
            flying_n = len(SPECIES) - len(self.app.cfg["hidden"])
            label = "Butterflies" if flying_n == len(SPECIES) else f"{flying_n} flying"
        layout = PangoCairo_layout(cr, label, 13)
        top = PAD + ROWS * CELL + (ROWS - 1) * GAP
        _text_w, text_h = layout.get_pixel_size()
        cr.set_source_rgba(0.96, 0.95, 0.92, 0.92)
        cr.move_to(PAD + 2, top + (CAPTION - text_h) / 2)
        PangoCairo_show(cr, layout)
        return True

    def _thumb(self, cr, name: str, cx: float, cy: float, box: float, flying: bool) -> None:
        surface = self.app.surfaces.get(name)
        if surface is None:
            return
        iw = surface.get_width()
        ih = surface.get_height()
        scale = box / max(iw, ih)
        cr.save()
        cr.arc(cx, cy, box * 0.46, 0, math.tau)
        cr.clip()
        cr.translate(cx, cy)
        cr.scale(scale, scale)
        cr.translate(-iw / 2, -ih / 2)
        pattern = cairo.SurfacePattern(surface)
        pattern.set_filter(cairo.FILTER_BILINEAR)
        cr.set_source(pattern)
        if flying:
            cr.paint()
        else:
            cr.paint_with_alpha(0.32)
        cr.restore()

    def _press(self, _widget, event) -> bool:
        if int(event.button) == 3:
            self._popup(event)
            return True
        if int(event.button) != 1:
            return False
        ox, oy = self.get_position()
        self._drag = {
            "x": float(event.x_root),
            "y": float(event.y_root),
            "ox": int(ox),
            "oy": int(oy),
            "moved": False,
        }
        return True

    def _motion(self, widget, event) -> bool:
        drag = self._drag
        if drag is None:
            hover = self.hit(float(event.x), float(event.y))
            if hover != self._hover:
                self._hover = hover
                if hover:
                    state = "flying" if self._flying(hover) else "hidden"
                    tip = f"{BY_ID[hover].name} · {state}"
                else:
                    tip = "Drag to move. Scroll to resize. Click a picture to show or hide it. The bar closes after you choose."
                widget.set_tooltip_text(tip)
                widget.queue_draw()
            return False
        dx = float(event.x_root) - drag["x"]
        dy = float(event.y_root) - drag["y"]
        if abs(dx) > 5 or abs(dy) > 5:
            drag["moved"] = True
        if drag["moved"]:
            self.move(int(drag["ox"] + dx), int(drag["oy"] + dy))
        return True

    def _release(self, _widget, event) -> bool:
        drag = self._drag
        self._drag = None
        if drag is None or int(event.button) != 1:
            return False
        if not drag["moved"]:
            hit = self.hit(float(event.x), float(event.y))
            if hit:
                hidden = list(self.app.cfg["hidden"])
                if hit in hidden:
                    hidden.remove(hit)
                    log(f"show {hit}")
                else:
                    hidden.append(hit)
                    log(f"hide {hit}")
                self.app.cfg["hidden"] = hidden
                save_cfg(self.app.cfg)
                self.app.sync_visible()
                self.note_selection()
            self.area.queue_draw()
            return True
        x, y = self.get_position()
        self.app.cfg["x"] = int(x)
        self.app.cfg["y"] = int(y)
        save_cfg(self.app.cfg)
        return True

    def _leave(self, _widget, _event) -> bool:
        if self._hover is not None:
            self._hover = None
            self.area.queue_draw()
        return False

    def _scroll(self, _widget, event) -> bool:
        direction = 0.0
        if event.direction == Gdk.ScrollDirection.UP:
            direction = 1.0
        elif event.direction == Gdk.ScrollDirection.DOWN:
            direction = -1.0
        elif event.direction == Gdk.ScrollDirection.SMOOTH:
            if event.delta_y < -0.2:
                direction = 1.0
            elif event.delta_y > 0.2:
                direction = -1.0
        if direction == 0.0:
            return False
        scale = float(self.app.cfg["scale"])
        scale *= 1.08 if direction > 0 else 0.92
        self.app.cfg["scale"] = clamp(scale, 0.55, 2.0)
        save_cfg(self.app.cfg)
        return True

    def _popup(self, event) -> None:
        menu = Gtk.Menu()
        self._menu = menu
        self._menu_lock = True
        above = Gtk.CheckMenuItem.new_with_label("Stay above windows")
        above.set_active(bool(self.app.cfg["above"]))
        above.connect("toggled", self._on_above)
        menu.append(above)
        show_all = Gtk.MenuItem.new_with_label("Show all")
        show_all.connect("activate", self._on_show_all)
        menu.append(show_all)
        hide_all = Gtk.MenuItem.new_with_label("Hide all")
        hide_all.connect("activate", self._on_hide_all)
        menu.append(hide_all)
        menu.append(Gtk.SeparatorMenuItem())
        group = None
        for label, value in SPEEDS:
            if group is None:
                item = Gtk.RadioMenuItem.new_with_label([], label)
                group = item.get_group()
            else:
                item = Gtk.RadioMenuItem.new_with_label(group, label)
                group = item.get_group()
            item.set_active(abs(float(self.app.cfg["speed"]) - value) < 0.05)
            item.connect("toggled", self._on_speed, value)
            menu.append(item)
        menu.append(Gtk.SeparatorMenuItem())
        login = Gtk.CheckMenuItem.new_with_label("Open at login")
        login.set_active(autostart_enabled())
        login.connect("toggled", self._on_login)
        menu.append(login)
        hint = Gtk.MenuItem.new_with_label("Click a butterfly to open this bar")
        hint.set_sensitive(False)
        menu.append(hint)
        menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem.new_with_label("Quit")
        quit_item.connect("activate", lambda *_: self.app.quit())
        menu.append(quit_item)
        menu.show_all()
        self._menu_lock = False
        menu.connect("deactivate", self._on_menu_done)
        menu.popup_at_pointer(event)

    def _on_above(self, item) -> None:
        if self._menu_lock:
            return
        self.app.cfg["above"] = bool(item.get_active())
        save_cfg(self.app.cfg)
        self.app.apply_layer()

    def _on_show_all(self, *_args) -> None:
        self.app.cfg["hidden"] = []
        save_cfg(self.app.cfg)
        self.app.sync_visible()
        self.area.queue_draw()
        self.note_selection()
        log("show all")

    def _on_hide_all(self, *_args) -> None:
        self.app.cfg["hidden"] = [item.id for item in SPECIES]
        save_cfg(self.app.cfg)
        self.app.sync_visible()
        self.area.queue_draw()
        self.cancel_hide()
        log("hide all")

    def note_selection(self) -> None:
        """A choice was made. Close the bar once the click has settled."""
        flying = len(SPECIES) - len(self.app.cfg["hidden"])
        if flying <= 0:
            self.cancel_hide()
            return
        self._schedule_hide()

    def cancel_hide(self) -> None:
        if self._hide_id:
            GLib.source_remove(self._hide_id)
            self._hide_id = None

    def _schedule_hide(self) -> None:
        self.cancel_hide()
        self._hide_id = GLib.timeout_add(SELECT_HIDE_MS, self._hide_after_select)

    def _menu_open(self) -> bool:
        menu = self._menu
        return menu is not None and menu.get_visible()

    def _on_menu_done(self, *_args) -> None:
        if self._hide_id:
            self._schedule_hide()

    def _hide_after_select(self) -> bool:
        self._hide_id = None
        flying = len(SPECIES) - len(self.app.cfg["hidden"])
        if not picker_should_hide(self._menu_open(), flying):
            if self._menu_open() and flying > 0:
                self._schedule_hide()
            return False
        x, y = self.get_position()
        self.app.cfg["x"] = int(x)
        self.app.cfg["y"] = int(y)
        save_cfg(self.app.cfg)
        self.hide()
        log("picker hide")
        return False

    def _on_speed(self, item, value: float) -> None:
        if self._menu_lock or not item.get_active():
            return
        self.app.cfg["speed"] = float(value)
        save_cfg(self.app.cfg)

    def _on_login(self, item) -> None:
        if self._menu_lock:
            return
        set_autostart(bool(item.get_active()))


def PangoCairo_layout(cr, text: str, size: float):
    layout = PangoCairo.create_layout(cr)
    layout.set_font_description(_font(size))
    layout.set_text(text, -1)
    return layout


def PangoCairo_show(cr, layout) -> None:
    PangoCairo.show_layout(cr, layout)


class App:
    def __init__(self) -> None:
        self.cfg = load_cfg()
        self.surfaces = {item.id: load_surface(item.id) for item in SPECIES}
        self.bounds = screen_bounds()
        self.flyers = []
        self.windows = []
        count = len(SPECIES)
        for index, species in enumerate(SPECIES):
            flyer = Flyer()
            flyer.place_start(self.bounds, index, count)
            self.flyers.append(flyer)
            self.windows.append(SpriteWindow(species.name, self._paint_for(index), self.show_picker))
        self._pos = [None] * count
        self._last = time.monotonic()
        self._pointer = None
        self.picker = Picker(self)
        self._place_picker()
        self.apply_layer()
        self._sync(0.0)
        self.sync_visible()
        if len(self.cfg["hidden"]) >= len(SPECIES):
            self.show_picker()
            log("picker shown because every butterfly is hidden")
        else:
            log("picker hidden until a butterfly is clicked")
        GLib.timeout_add(33, self._tick)
        hidden = ",".join(self.cfg["hidden"]) or "none"
        log(f"started butterflies scale={self.cfg['scale']} hidden={hidden}")

    def _paint_for(self, index: int):
        def paint(cr, width, height, index=index):
            self._paint_fly(cr, width, height, index)
        return paint

    def _place_picker(self) -> None:
        x, y = self.cfg.get("x"), self.cfg.get("y")
        if not isinstance(x, int) or not isinstance(y, int):
            wx, wy, ww, wh = work_area()
            x = int(wx + max(8, (ww - PICKER_W) / 2))
            y = int(wy + max(8, wh - PICKER_H - 20))
            self.cfg["x"] = x
            self.cfg["y"] = y
            save_cfg(self.cfg)
        self.picker.move(int(x), int(y))

    def apply_layer(self) -> None:
        above = bool(self.cfg["above"])
        for window in (*self.windows, self.picker):
            window.set_keep_above(above)
            if not above:
                sink_above_icons(window)
        log("layer " + ("above" if above else "desklet"))

    def sync_visible(self) -> None:
        hidden = set(self.cfg["hidden"])
        for index, species in enumerate(SPECIES):
            window = self.windows[index]
            want = species.id not in hidden
            if want:
                self._move_sprite(index)
                if not window.get_visible():
                    window.show_all()
            elif window.get_visible():
                window.hide()
        self.apply_layer()

    def _tick(self) -> bool:
        global _raise_picker
        now = time.monotonic()
        dt = min(0.05, max(0.0, now - self._last))
        self._last = now
        if dt <= 0.0:
            return True
        self.bounds = screen_bounds()
        point = pointer_xy()
        if point is None:
            mx, my = -10_000.0, -10_000.0
        else:
            mx, my = point
        scale = float(self.cfg["scale"])
        speed = float(self.cfg["speed"])
        self._arm_holds(mx, my)
        flyers = self.flyers
        for index, flyer in enumerate(flyers):
            others = flyers[:index] + flyers[index + 1 :]
            flyer.update(dt, SPECIES[index], scale, speed, mx, my, self.bounds, tuple(others))
        self._sync(dt)
        self._refresh_hits()
        if _raise_picker:
            _raise_picker = False
            self.show_picker()
        return True

    def show_picker(self) -> None:
        self.picker.cancel_hide()
        if not self.picker.get_visible():
            self._place_picker()
            self.picker.show_all()
            self.picker.resize(PICKER_W, PICKER_H)
        self.picker.present()
        log("picker show")

    def _arm_holds(self, mx: float, my: float) -> None:
        """Pause a butterfly as the pointer reaches it, long enough to click."""
        now = time.monotonic()
        hidden = set(self.cfg["hidden"])
        for index, species in enumerate(SPECIES):
            flyer = self.flyers[index]
            if species.id in hidden:
                flyer.hold_until = 0.0
                continue
            dist = math.hypot(flyer.x - mx, flyer.y - my)
            if dist < FLEE_R * 1.15:
                if flyer.hold_until == 0.0:
                    flyer.hold_until = now + HOLD_SEC
            elif dist > FLEE_R * 1.45:
                flyer.hold_until = 0.0

    def _refresh_hits(self) -> None:
        scale = float(self.cfg["scale"])
        hidden = set(self.cfg["hidden"])
        for index, species in enumerate(SPECIES):
            window = self.windows[index]
            if species.id in hidden or not window.get_visible():
                continue
            surface = self.surfaces[species.id]
            draw_w, draw_h, _ww, _hh, _iw, _ih = sprite_metrics(species, surface, scale)
            flyer = self.flyers[index]
            flap = 0.68 + 0.32 * (0.5 + 0.5 * math.sin(flyer.flap))
            width, height = window.get_size()
            window.set_hit(
                draw_w * 0.52,
                max(8.0, draw_h * flap * 0.52),
                width / 2,
                height / 2 - flyer.flutter * 0.35,
                int(width),
                int(height),
            )

    def _sync(self, _dt: float) -> None:
        hidden = set(self.cfg["hidden"])
        for index, species in enumerate(SPECIES):
            if species.id in hidden:
                continue
            self._move_sprite(index)
            self.windows[index].queue_draw()

    def _move_sprite(self, index: int) -> None:
        species = SPECIES[index]
        window = self.windows[index]
        surface = self.surfaces[species.id]
        _dw, _dh, win_w, win_h, _iw, _ih = sprite_metrics(species, surface, float(self.cfg["scale"]))
        cw, ch = window.get_size()
        if int(cw) != win_w or int(ch) != win_h:
            window.set_size_request(win_w, win_h)
            window.resize(win_w, win_h)
        flyer = self.flyers[index]
        origin = (int(flyer.x - win_w / 2), int(flyer.y - win_h / 2))
        if self._pos[index] != origin:
            window.move(*origin)
            self._pos[index] = origin

    def _paint_fly(self, cr: cairo.Context, width: int, height: int, index: int) -> None:
        species = SPECIES[index]
        flyer = self.flyers[index]
        surface = self.surfaces[species.id]
        scale = float(self.cfg["scale"])
        draw_w, draw_h, _ww, _hh, iw, ih = sprite_metrics(species, surface, scale)
        cx = width / 2
        cy = height / 2
        flap = 0.68 + 0.32 * (0.5 + 0.5 * math.sin(flyer.flap))
        drop_x = clamp(flyer.shadow_x - flyer.x, -48.0, 48.0)
        drop_y = clamp(flyer.shadow_y - flyer.y, 8.0, 52.0)
        soft_ellipse(
            cr,
            cx + drop_x,
            cy + drop_y,
            draw_w * 0.34 * flap,
            max(7.0, draw_h * 0.045),
            0.16,
        )
        paint_sprite(
            cr,
            surface,
            cx,
            cy - flyer.flutter * 0.35,
            draw_w,
            draw_h * flap,
            iw,
            ih,
            flyer.flip,
            flyer.bank,
            1.0,
        )

    def quit(self) -> bool:
        log("quit")
        release_pid()
        for window in self.windows:
            window.destroy()
        self.picker.destroy()
        Gtk.main_quit()
        return False


def _on_usr1(_signum, _frame) -> None:
    global _raise_picker
    _raise_picker = True


def _on_term(_signum, _frame) -> None:
    GLib.idle_add(Gtk.main_quit)


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        return self_test()
    os.environ.setdefault("GDK_BACKEND", "x11")
    GLib.set_prgname(PRGNAME)
    try:
        Gdk.set_program_class(PRGNAME)
    except Exception:
        pass
    write_icon()
    install_launchers()
    claim_pid("--replace" in argv)
    signal.signal(signal.SIGUSR1, _on_usr1)
    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)
    app = App()
    try:
        Gtk.main()
    finally:
        release_pid()
        app.picker.destroy()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except Exception:
        import traceback

        log(traceback.format_exc())
        raise
