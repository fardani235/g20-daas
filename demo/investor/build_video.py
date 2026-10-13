#!/usr/bin/env python3
"""Assemble investor-demo.mp4 from recordings/ and edit.json.

    python3 build_video.py              build everything
    python3 build_video.py --only 7,8   re-render parts 7 and 8, then reassemble
    python3 build_video.py --draft      fast, lower-quality encode for checking an edit
    python3 build_video.py --stills     only draw cards and captions into build/ (no video)
    python3 build_video.py --timeline   only write build/timeline.md and the .srt, and check narration fits

The edit is data: edit.json lists the parts of the video in order — title and
chapter cards, and clips cut from the scene recordings — with their captions,
zooms and narration. See README.md for the format.

Local tools only: FFmpeg for video, Pillow for cards and captions.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent
REC = ROOT / "recordings"
BUILD = ROOT / "build"
AUDIO = ROOT / "audio"
W, H, FPS = 1920, 1080, 30
WORDS_PER_SECOND = 2.6  # unhurried presentation pace, used to check narration fits

# ----------------------------------------------------------------- look & feel
# Colours follow the product's own public pages: deep navy, cyan-to-blue accent.
NAVY_TOP, NAVY_BOTTOM = (6, 10, 24), (12, 20, 44)
CYAN, BLUE = (34, 211, 238), (59, 130, 246)
WHITE, SLATE, MUTED = (255, 255, 255), (203, 213, 225), (136, 153, 180)

FONT_DIRS = [Path("/usr/share/fonts/truetype/lato"), Path("/usr/share/fonts/truetype/dejavu")]
FONT_FILES = {
    "regular": ["Lato-Regular.ttf", "DejaVuSans.ttf"],
    "medium": ["Lato-Medium.ttf", "DejaVuSans.ttf"],
    "semibold": ["Lato-Semibold.ttf", "DejaVuSans-Bold.ttf"],
    "bold": ["Lato-Bold.ttf", "DejaVuSans-Bold.ttf"],
    "heavy": ["Lato-Heavy.ttf", "DejaVuSans-Bold.ttf"],
    "light": ["Lato-Light.ttf", "DejaVuSans-ExtraLight.ttf"],
}


def font(weight: str, size: float) -> ImageFont.FreeTypeFont:
    for name in FONT_FILES[weight]:
        for d in FONT_DIRS:
            if (d / name).exists():
                return ImageFont.truetype(str(d / name), int(round(size)))
    return ImageFont.load_default()


def lerp(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def text_width(text: str, f, tracking: float = 0.0) -> float:
    if not tracking:
        return f.getlength(text)
    return sum(f.getlength(ch) + tracking for ch in text) - tracking


def draw_text(draw, xy, text, f, fill, tracking: float = 0.0, anchor="ls"):
    """Text with optional letter-spacing. xy is the left end of the baseline."""
    x, y = xy
    if not tracking:
        draw.text((x, y), text, font=f, fill=fill, anchor=anchor)
        return
    for ch in text:
        draw.text((x, y), ch, font=f, fill=fill, anchor=anchor)
        x += f.getlength(ch) + tracking


def gradient_text(img, xy, text, f, c0=CYAN, c1=BLUE):
    """Paint text filled with a left-to-right gradient onto an RGBA image."""
    x, y = xy
    w = int(math.ceil(f.getlength(text))) + 8
    asc, desc = f.getmetrics()
    h = asc + desc + 8
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).text((0, asc), text, font=f, fill=255, anchor="ls")
    grad = Image.new("RGBA", (w, h))
    gd = ImageDraw.Draw(grad)
    for i in range(w):
        gd.line([(i, 0), (i, h)], fill=lerp(c0, c1, i / max(1, w - 1)) + (255,))
    img.paste(grad, (int(x), int(y - asc)), mask)
    return w - 8


def background(scale: int = 2) -> Image.Image:
    """Navy field with two soft glows and a faint survey grid."""
    w, h = W * scale, H * scale
    small = Image.new("RGB", (W // 4, H // 4))
    px = small.load()
    sw, sh = small.size
    for y in range(sh):
        base = lerp(NAVY_TOP, NAVY_BOTTOM, y / (sh - 1))
        for x in range(sw):
            g1 = max(0.0, 1 - math.hypot((x - sw * 0.82) / (sw * 0.55), (y - sh * 0.12) / (sh * 0.75)))
            g2 = max(0.0, 1 - math.hypot((x - sw * 0.10) / (sw * 0.50), (y - sh * 0.95) / (sh * 0.70)))
            r = base[0] + 10 * g1 ** 2 + 14 * g2 ** 2
            g = base[1] + 46 * g1 ** 2 + 22 * g2 ** 2
            b = base[2] + 58 * g1 ** 2 + 60 * g2 ** 2
            px[x, y] = (int(min(255, r)), int(min(255, g)), int(min(255, b)))
    img = small.resize((w, h), Image.BICUBIC).filter(ImageFilter.GaussianBlur(8 * scale)).convert("RGBA")
    grid = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grid)
    step = 96 * scale
    for x in range(0, w, step):
        gd.line([(x, 0), (x, h)], fill=(255, 255, 255, 9), width=scale)
    for y in range(0, h, step):
        gd.line([(0, y), (w, y)], fill=(255, 255, 255, 9), width=scale)
    return Image.alpha_composite(img, grid)


def brand_mark(img, x, y, size):
    """Rounded gradient tile with a folded-map glyph, echoing the app's logo."""
    tile = Image.new("RGBA", (size, size))
    td = ImageDraw.Draw(tile)
    for i in range(size):
        td.line([(i, 0), (i, size)], fill=lerp(CYAN, BLUE, i / (size - 1)) + (255,))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=size * 0.26, fill=255)
    img.paste(tile, (int(x), int(y)), mask)
    d = ImageDraw.Draw(img)
    u = size / 24.0
    pts = [(4.5, 7.5), (9.5, 5.5), (14.5, 7.5), (19.5, 5.5), (19.5, 16.5), (14.5, 18.5), (9.5, 16.5), (4.5, 18.5)]
    poly = [(x + px_ * u, y + py_ * u) for px_, py_ in pts]
    lw = max(2, int(round(1.7 * u)))
    d.line(poly + [poly[0]], fill=WHITE, width=lw, joint="curve")
    d.line([(x + 9.5 * u, y + 5.5 * u), (x + 9.5 * u, y + 16.5 * u)], fill=WHITE, width=lw)
    d.line([(x + 14.5 * u, y + 7.5 * u), (x + 14.5 * u, y + 18.5 * u)], fill=WHITE, width=lw)


def lockup(img, cx, y, s, brand):
    """Logo tile + product name + descriptor, centred on cx with top at y."""
    d = ImageDraw.Draw(img)
    name_f, tag_f = font("bold", 46 * s), font("semibold", 17 * s)
    name, accent = brand["name"], brand.get("name_accent", "")
    tile = int(64 * s)
    gap = int(20 * s)
    name_w = text_width(name + (" " + accent if accent else ""), name_f)
    tag = brand.get("descriptor", "").upper()
    tag_w = text_width(tag, tag_f, 3.2 * s)
    block_w = tile + gap + max(name_w, tag_w)
    x = cx - block_w / 2
    brand_mark(img, x, y, tile)
    tx = x + tile + gap
    d.text((tx, y + 36 * s), name, font=name_f, fill=WHITE, anchor="ls")
    if accent:
        d.text((tx + name_f.getlength(name + " "), y + 36 * s), accent, font=name_f, fill=CYAN, anchor="ls")
    draw_text(d, (tx, y + 62 * s), tag, tag_f, MUTED, tracking=3.2 * s)


def stepper(img, cx, y, s, steps, current):
    """The five-step story as a row of pills. `current` is 1-based; 0 = all lit."""
    d = ImageDraw.Draw(img)
    f = font("semibold", 21 * s)
    pad_x, hgt, gap = 26 * s, 50 * s, 48 * s
    widths = [text_width(t, f) + 2 * pad_x for t in steps]
    total = sum(widths) + gap * (len(steps) - 1)
    x = cx - total / 2
    for i, (label, wd) in enumerate(zip(steps, widths), start=1):
        lit = current == 0 or i == current
        done = current and i < current
        box = [x, y, x + wd, y + hgt]
        if lit:
            pill = Image.new("RGBA", (int(wd), int(hgt)))
            pd = ImageDraw.Draw(pill)
            for k in range(int(wd)):
                pd.line([(k, 0), (k, hgt)], fill=lerp(CYAN, BLUE, k / max(1, wd - 1)) + (255,))
            m = Image.new("L", pill.size, 0)
            ImageDraw.Draw(m).rounded_rectangle([0, 0, pill.size[0] - 1, pill.size[1] - 1], radius=hgt / 2, fill=255)
            img.paste(pill, (int(x), int(y)), m)
            colour = (6, 12, 28)
        else:
            d.rounded_rectangle(box, radius=hgt / 2, outline=(255, 255, 255, 70 if done else 40), width=max(1, int(1.5 * s)))
            colour = SLATE if done else MUTED
        d.text((x + wd / 2, y + hgt / 2), label, font=f, fill=colour, anchor="mm")
        x += wd
        if i < len(steps):
            ax0, ax1, ay = x + 12 * s, x + gap - 12 * s, y + hgt / 2
            ac = (255, 255, 255, 120 if (current == 0 or i < current) else 55)
            d.line([(ax0, ay), (ax1, ay)], fill=ac, width=max(1, int(2 * s)))
            d.line([(ax1 - 7 * s, ay - 6 * s), (ax1, ay), (ax1 - 7 * s, ay + 6 * s)], fill=ac, width=max(1, int(2 * s)))
            x += gap


def wrap(text: str, f, max_w: float) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = (line + " " + word).strip()
        if line and f.getlength(trial) > max_w:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + ([line] if line else [])


def card_title(spec, brand, steps, s=2) -> Image.Image:
    img = background(s)
    d = ImageDraw.Draw(img)
    cx = W * s / 2
    lockup(img, cx, 250 * s, s * 1.15, brand)
    hf = font("heavy", 88 * s)
    y = 520 * s
    for line in spec["headline"]:
        if isinstance(line, dict):  # {"accent": "..."} → gradient line
            text = line["accent"]
            gradient_text(img, (cx - hf.getlength(text) / 2, y), text, hf)
        else:
            d.text((cx, y), line, font=hf, fill=WHITE, anchor="ms")
        y += 104 * s
    if spec.get("sub"):
        d.text((cx, y + 4 * s), spec["sub"], font=font("regular", 30 * s), fill=SLATE, anchor="ms")
    stepper(img, cx, 880 * s, s * 0.9, steps, 0)
    return img


def card_chapter(spec, brand, steps, s=2) -> Image.Image:
    img = background(s)
    d = ImageDraw.Draw(img)
    cx = W * s / 2
    n = spec["n"]
    draw_text(d, (cx - text_width(f"CHAPTER {n:02d}", font("semibold", 22 * s), 6 * s) / 2, 392 * s),
              f"CHAPTER {n:02d}", font("semibold", 22 * s), CYAN, tracking=6 * s)
    d.text((cx, 500 * s), spec["title"], font=font("heavy", 92 * s), fill=WHITE, anchor="ms")
    if spec.get("sub"):
        d.text((cx, 566 * s), spec["sub"], font=font("regular", 32 * s), fill=SLATE, anchor="ms")
    stepper(img, cx, 720 * s, s * 0.9, steps, n)
    return img


def card_closing(spec, brand, steps, s=2) -> Image.Image:
    img = background(s)
    d = ImageDraw.Draw(img)
    cx = W * s / 2
    lockup(img, cx, 240 * s, s * 1.15, brand)
    hf = font("heavy", 70 * s)
    y = 500 * s
    for line in spec["headline"]:
        if isinstance(line, dict):
            text = line["accent"]
            gradient_text(img, (cx - hf.getlength(text) / 2, y), text, hf)
        else:
            d.text((cx, y), line, font=hf, fill=WHITE, anchor="ms")
        y += 88 * s
    stepper(img, cx, y + 18 * s, s * 0.9, steps, 0)
    yy = y + 150 * s
    for line in spec.get("notes", []):
        d.text((cx, yy), line, font=font("regular", 26 * s), fill=MUTED, anchor="ms")
        yy += 40 * s
    return img


def caption_image(cap, chapter_label: str | None) -> Image.Image:
    """A lower-third: chapter tag, headline, optional supporting line."""
    s = 2  # draw at 2x, downsample for clean edges
    tag_f, title_f, text_f = font("bold", 17 * s), font("bold", 35 * s), font("regular", 25 * s)
    max_w = cap.get("max_width", 1180) * s
    title_lines = wrap(cap["title"], title_f, max_w)
    text_lines = wrap(cap.get("text", ""), text_f, max_w)
    tag = (cap.get("tag") or chapter_label or "").upper()
    pad_x, pad_y, bar = 34 * s, 24 * s, 7 * s
    inner_w = max([text_width(tag, tag_f, 3 * s)] + [title_f.getlength(t) for t in title_lines]
                  + [text_f.getlength(t) for t in text_lines])
    w = int(inner_w + 2 * pad_x + bar + 14 * s)
    h = int(2 * pad_y + (26 * s if tag else 0) + 46 * s * len(title_lines) + (8 * s + 35 * s * len(text_lines) if text_lines else 0))
    margin = 40 * s  # room for the shadow
    img = Image.new("RGBA", (w + 2 * margin, h + 2 * margin), (0, 0, 0, 0))
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([margin, margin + 10 * s, margin + w, margin + h + 10 * s], radius=20 * s, fill=(0, 0, 0, 150))
    img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(16 * s)))
    d = ImageDraw.Draw(img)
    box = [margin, margin, margin + w, margin + h]
    d.rounded_rectangle(box, radius=20 * s, fill=(9, 14, 30, 232), outline=(255, 255, 255, 34), width=s)
    # accent bar
    bar_img = Image.new("RGBA", (int(bar), h - 2 * int(16 * s)))
    bd = ImageDraw.Draw(bar_img)
    for k in range(bar_img.size[1]):
        bd.line([(0, k), (bar, k)], fill=lerp(CYAN, BLUE, k / max(1, bar_img.size[1] - 1)) + (255,))
    bm = Image.new("L", bar_img.size, 0)
    ImageDraw.Draw(bm).rounded_rectangle([0, 0, bar_img.size[0] - 1, bar_img.size[1] - 1], radius=bar / 2, fill=255)
    img.paste(bar_img, (int(margin + 16 * s), int(margin + 16 * s)), bm)
    x = margin + 16 * s + bar + 20 * s
    y = margin + pad_y
    if tag:
        draw_text(d, (x, y + 15 * s), tag, tag_f, CYAN, tracking=3 * s)
        y += 26 * s
    for line in title_lines:
        d.text((x, y + 36 * s), line, font=title_f, fill=WHITE, anchor="ls")
        y += 46 * s
    if text_lines:
        y += 8 * s
        for line in text_lines:
            d.text((x, y + 26 * s), line, font=text_f, fill=SLATE, anchor="ls")
            y += 35 * s
    return img.resize((img.size[0] // s, img.size[1] // s), Image.LANCZOS)


CAPTION_MARGIN = 40  # transparent border around the pill (shadow room), in output px


def caption_xy(cap, size):
    """Top-left of the caption image for a named or explicit position."""
    w, h = size
    pos = cap.get("pos", "bottom")
    if isinstance(pos, list):
        cx, cy = pos
        return int(cx - w / 2), int(cy - h / 2)
    cx = {"left": 400 + (w - 2 * CAPTION_MARGIN) / 2, "right": W - 60 - (w - 2 * CAPTION_MARGIN) / 2,
          "map": 1040}.get(cap.get("align", "center"), W / 2)
    if pos == "top":
        y = 150 - CAPTION_MARGIN
    elif pos == "middle":
        y = (H - h) / 2
    else:
        y = H - 52 - h + CAPTION_MARGIN
    return int(cx - w / 2), int(y)


# ------------------------------------------------------------------- ffmpeg
def run(cmd: list[str], quiet=True):
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        sys.stderr.write("\n$ " + " ".join(cmd) + "\n" + proc.stderr[-3000:] + "\n")
        raise SystemExit(f"ffmpeg failed ({proc.returncode})")
    return proc


def frames(seconds: float) -> int:
    return max(1, int(round(seconds * FPS)))


def resolve(expr, marks: dict) -> float:
    """'name', 'name+0.5', 'name-0.3' or a number → seconds of playback time."""
    if isinstance(expr, (int, float)):
        return float(expr)
    m = re.fullmatch(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:([+-])\s*([0-9.]+))?\s*", str(expr))
    if not m or m.group(1) not in marks:
        raise SystemExit(f"unknown mark {expr!r}; scene has {sorted(k for k in marks if k not in ('name', 'slow'))}")
    t = float(marks[m.group(1)])
    if m.group(2):
        t += float(m.group(3)) * (1 if m.group(2) == "+" else -1)
    return t


def zoom_exprs(zooms, start_offset=0.0):
    """ffmpeg expressions (zoom factor, crop x, crop y) for eased zoom windows."""
    if not zooms:
        return None
    s_terms, z_terms, x_terms, y_terms = [], [], [], []
    for z in zooms:
        a, d = z["at"] - start_offset, z["dur"]
        b, e = a + d, min(z.get("ease", 0.7), d / 2)
        x, y, w, h = z["rect"]
        factor = min(W / w, H / h, z.get("max", 2.2))
        vw, vh = W / factor, H / factor
        rx = min(max(x + w / 2 - vw / 2, 0), W - vw)
        ry = min(max(y + h / 2 - vh / 2, 0), H - vh)
        p = f"(clip((t-{a:.3f})/{e:.3f},0,1)*clip(({b:.3f}-t)/{e:.3f},0,1))"
        s = f"({p}*{p}*(3-2*{p}))"
        z_terms.append(f"{factor - 1:.5f}*{s}")
        # Crop origin in the scaled frame. Linear in (zoom - 1), so a window
        # that touches a frame edge stays on that edge all the way in and out.
        x_terms.append(f"{rx * factor:.2f}*{s}")
        y_terms.append(f"{ry * factor:.2f}*{s}")
    zf = "(1+" + "+".join(z_terms) + ")"
    return zf, "(" + "+".join(x_terms) + ")", "(" + "+".join(y_terms) + ")"


def zoom_filter(zooms):
    ex = zoom_exprs(zooms)
    if not ex:
        return ""
    zf, xf, yf = ex
    # Scale the whole frame up by the eased factor, then take a fixed 1920x1080
    # window out of it. Even dimensions keep 4:2:0 chroma aligned.
    return (f",scale=w='2*trunc({W}*{zf}/2)':h='2*trunc({H}*{zf}/2)':eval=frame:flags=lanczos"
            f",crop={W}:{H}:x='{xf}':y='{yf}'")


JOIN = 0.4  # dissolve across a skipped stretch of a recording, seconds


class ClipTime:
    """Where a clip's footage comes from, and scene time -> clip time.

    A clip is `in`..`out` of one recording, minus any `skip` ranges (waiting
    that should not be watched, e.g. map tiles sharpening). The pieces either
    side of a skip are dissolved together over JOIN seconds.
    """

    def __init__(self, seg, marks):
        t_in, t_out = resolve(seg["in"], marks), resolve(seg["out"], marks)
        if t_out <= t_in:
            raise SystemExit(f"{seg['scene']}: out {t_out} is not after in {t_in}")
        self.pieces, cur = [], t_in
        for a, b in sorted((resolve(a, marks), resolve(b, marks)) for a, b in seg.get("skip", [])):
            if not (cur < a < b < t_out):
                raise SystemExit(f"{seg['scene']}: skip {a:.2f}..{b:.2f} is outside {cur:.2f}..{t_out:.2f}")
            self.pieces.append((cur, a))
            cur = b
        self.pieces.append((cur, t_out))
        if any(b - a <= JOIN for a, b in self.pieces) and len(self.pieces) > 1:
            raise SystemExit(f"{seg['scene']}: a piece next to a skip is shorter than {JOIN}s")
        self.marks = marks
        self.dur = frames(sum(b - a for a, b in self.pieces) - JOIN * (len(self.pieces) - 1)) / FPS

    def at(self, expr, default=None):
        """Clip time of a caption/zoom position: a mark expression, or seconds from the clip start."""
        if expr is None:
            return default
        if not isinstance(expr, str):
            return float(expr)
        t, off = resolve(expr, self.marks), 0.0
        for k, (a, b) in enumerate(self.pieces):
            if t <= b or k == len(self.pieces) - 1:
                return off + max(0.0, t - a)  # a time inside a skip lands on the join
            off += (b - a) - JOIN


class Builder:
    def __init__(self, edit: dict, draft: bool):
        self.edit = edit
        self.brand = edit["brand"]
        self.steps = edit["steps"]
        self.draft = draft
        self.x264 = ["-c:v", "libx264", "-preset", "veryfast" if draft else "medium",
                     "-crf", "26" if draft else "15", "-pix_fmt", "yuv420p", "-r", str(FPS)]
        self.chapter = None  # (n, title) of the chapter the current part belongs to
        BUILD.mkdir(exist_ok=True)

    # -- parts ---------------------------------------------------------------
    def part_path(self, i):
        return BUILD / f"part_{i:02d}.mp4"

    def chapter_label(self):
        if not self.chapter:
            return None
        return f"{self.chapter[0]:02d}  {self.chapter[1]}"

    def render_card(self, i, seg, stills_only=False):
        kind = seg["type"]
        img = {"title": card_title, "chapter": card_chapter, "closing": card_closing}[kind](seg, self.brand, self.steps)
        png = BUILD / f"part_{i:02d}_{kind}.png"
        img.convert("RGB").save(png)
        if stills_only:
            return
        n = frames(seg["dur"])
        # A slow push-in keeps a still card alive; drawn at 2x so it stays sharp.
        vf = (f"zoompan=z='1+0.035*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={n}:s={W}x{H}:fps={FPS},"
              f"format=yuv420p")
        run(["ffmpeg", "-y", "-v", "error", "-i", str(png), "-vf", vf, "-frames:v", str(n), *self.x264, str(self.part_path(i))])

    def render_clip(self, i, seg, stills_only=False):
        scene = seg["scene"]
        marks = json.loads((REC / f"{scene}.json").read_text())
        slow = float(marks.get("slow", 1))
        ct = ClipTime(seg, marks)
        dur = seg["_dur"] = ct.dur

        caps = []
        for k, cap in enumerate(seg.get("captions", [])):
            img = caption_image(cap, self.chapter_label())
            png = BUILD / f"part_{i:02d}_cap{k}.png"
            img.save(png)
            a = max(0.0, ct.at(cap.get("at"), 0.45))
            end = min(dur, ct.at(cap.get("until"), dur - 0.45))
            caps.append((png, img.size, a, end, cap))
        if stills_only:
            return

        zooms = [dict(z, at=ct.at(z["at"])) for z in seg.get("zoom", [])]

        # One input per piece of footage, played back at normal speed.
        cmd = ["ffmpeg", "-y", "-v", "error"]
        chain = ""
        for k, (a, b) in enumerate(ct.pieces):
            cmd += ["-ss", f"{a * slow:.3f}", "-t", f"{(b - a) * slow + 0.5:.3f}", "-i", str(REC / f"{scene}.webm")]
            chain += (f"[{k}:v]setpts=(PTS-STARTPTS)/{slow:g},fps={FPS},trim=duration={b - a:.3f},setpts=PTS-STARTPTS,"
                      f"scale={W}:{H}:flags=lanczos,settb=AVTB,format=yuv420p[s{k}];")
        last, offset = "s0", 0.0
        for k in range(1, len(ct.pieces)):
            offset += (ct.pieces[k - 1][1] - ct.pieces[k - 1][0]) - JOIN
            chain += f"[{last}][s{k}]xfade=transition=fade:duration={JOIN}:offset={offset:.3f}[j{k}];"
            last = f"j{k}"
        n = len(ct.pieces)
        for png, _, _, _, _ in caps:
            cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{dur + 1:.3f}", "-i", str(png)]
        chain += f"[{last}]null{zoom_filter(zooms)},setsar=1,format=yuv420p[v0]"
        last = "v0"
        fade = 0.32
        for k, (_, size, a, end, cap) in enumerate(caps, start=1):
            x, y = caption_xy(cap, size)
            chain += (f";[{n + k - 1}:v]format=rgba,fade=t=in:st={a:.3f}:d={fade}:alpha=1,"
                      f"fade=t=out:st={max(a, end - fade):.3f}:d={fade}:alpha=1[c{k}]")
            # settles upward by 14px as it fades in
            rise = f"{y}+14*pow(1-clip((t-{a:.3f})/{fade},0,1),2)"
            chain += f";[{last}][c{k}]overlay=x={x}:y='{rise}':enable='between(t,{a:.3f},{end:.3f})'[v{k}]"
            last = f"v{k}"
        cmd += ["-filter_complex", chain, "-map", f"[{last}]", "-frames:v", str(frames(dur)), "-an", *self.x264, str(self.part_path(i))]
        run(cmd)

    def render(self, only=None, stills_only=False):
        for i, seg in enumerate(self.edit["timeline"]):
            if seg["type"] == "chapter":
                self.chapter = (seg["n"], seg["title"])
            skip = only is not None and i not in only
            if seg["type"] == "clip":
                if skip:
                    # still needed for the timeline
                    seg["_dur"] = ClipTime(seg, json.loads((REC / f"{seg['scene']}.json").read_text())).dur
                    continue
                print(f"  part {i:02d}  clip   {seg['scene']:<16} {seg['in']} → {seg['out']}", flush=True)
                self.render_clip(i, seg, stills_only)
            else:
                seg["_dur"] = frames(seg["dur"]) / FPS
                if skip:
                    continue
                print(f"  part {i:02d}  {seg['type']:<6} {seg.get('title', '')}", flush=True)
                self.render_card(i, seg, stills_only)

    # -- timeline ------------------------------------------------------------
    def transitions(self):
        """Cross-fade length into each part (index 0 has none)."""
        tl = self.edit["timeline"]
        d = self.edit.get("transitions", {})
        out = [0.0]
        for prev, cur in zip(tl, tl[1:]):
            if "transition" in cur:
                t = float(cur["transition"])
            elif prev["type"] != "clip" or cur["type"] != "clip":
                t = float(d.get("card", 0.5))
            else:
                t = float(d.get("clip", 0.35))
            out.append(min(t, prev["_dur"] / 2, cur["_dur"] / 2))
        return out

    def schedule(self):
        """[(start, end)] of every part on the final timeline."""
        tl, tr = self.edit["timeline"], self.transitions()
        times, t = [], 0.0
        for i, seg in enumerate(tl):
            start = t - tr[i] if i else 0.0
            times.append((start, start + seg["_dur"]))
            t = start + seg["_dur"]
        return times, tr

    def assemble(self, out: Path):
        tl = self.edit["timeline"]
        times, tr = self.schedule()
        total = times[-1][1]
        cmd = ["ffmpeg", "-y", "-v", "error"]
        for i in range(len(tl)):
            cmd += ["-i", str(self.part_path(i))]
        # xfade needs every input on the same timebase and frame rate.
        graph = "".join(f"[{i}:v]settb=AVTB,fps={FPS},format=yuv420p[p{i}];" for i in range(len(tl)))
        last = "p0"
        for i in range(1, len(tl)):
            graph += f"[{last}][p{i}]xfade=transition=fade:duration={tr[i]:.3f}:offset={times[i][0]:.3f}[x{i}];"
            last = f"x{i}"
        fade_out = float(self.edit.get("fade_out", 0.8))
        graph += f"[{last}]fade=t=in:st=0:d=0.5,fade=t=out:st={total - fade_out:.3f}:d={fade_out},format=yuv420p[v]"

        # Audio: narration and music are optional files; otherwise a silent track
        # so every player shows a normal, seekable file.
        narration = next((p for p in sorted(AUDIO.glob("narration.*"))), None) if AUDIO.exists() else None
        music = next((p for p in sorted(AUDIO.glob("music.*"))), None) if AUDIO.exists() else None
        n = len(tl)
        if narration or music:
            mix, idx = [], n
            if narration:
                cmd += ["-i", str(narration)]
                graph += f";[{idx}:a]aresample=48000,apad[na]"
                mix.append("[na]"); idx += 1
            if music:
                cmd += ["-stream_loop", "-1", "-i", str(music)]
                vol = 0.16 if narration else 0.5
                graph += (f";[{idx}:a]aresample=48000,volume={vol},afade=t=in:st=0:d=1.5,"
                          f"afade=t=out:st={total - 2:.3f}:d=2[mu]")
                mix.append("[mu]")
            graph += f";{''.join(mix)}amix=inputs={len(mix)}:duration=longest:normalize=0,atrim=0:{total:.3f}[a]"
            audio_map = ["-map", "[a]"]
        else:
            cmd += ["-f", "lavfi", "-t", f"{total:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
            audio_map = ["-map", f"{n}:a"]
        cmd += ["-filter_complex", graph, "-map", "[v]", *audio_map,
                "-c:v", "libx264", "-preset", "veryfast" if self.draft else "medium", "-crf", "28" if self.draft else "21",
                "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", str(FPS), "-g", str(FPS * 2),
                "-c:a", "aac", "-b:a", "160k", "-t", f"{total:.3f}", "-movflags", "+faststart", str(out)]
        run(cmd)
        return total

    def write_timeline(self):
        times, tr = self.schedule()
        tl = self.edit["timeline"]
        rows, cues = [], []
        narrated = [i for i, seg in enumerate(tl) if seg.get("narration", "").strip()]
        for i, (seg, (a, b)) in enumerate(zip(tl, times)):
            label = seg.get("scene") or seg["type"]
            rows.append({"part": i, "type": seg["type"], "label": label, "start": round(a, 2), "end": round(b, 2),
                         "captions": [c["title"] for c in seg.get("captions", [])], "narration": seg.get("narration", "")})
            text = seg.get("narration", "").strip()
            if not text:
                continue
            # A line may run on over a following card: its window lasts until
            # the next part that has narration of its own.
            later = [j for j in narrated if j > i]
            start = a + (0.3 if i else 0.4)
            end = times[later[0]][0] if later else times[-1][1] - 0.8
            words = len(re.findall(r"[\w'-]+", text))
            rows[-1]["narration_window"] = [round(start, 2), round(end, 2)]
            if words / WORDS_PER_SECOND > end - start + 0.05:
                print(f"  note: narration of part {i} ({label}) is {words} words for a {end - start:.1f}s window")
            # one subtitle cue per sentence, time shared by length
            parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]
            span = min(end - start, max(words / WORDS_PER_SECOND + 0.6, 2.0))
            weight = sum(len(p) for p in parts)
            t = start
            for p in parts:
                dt = span * len(p) / weight
                cues.append((t, t + dt - 0.05, p))
                t += dt
        (BUILD / "timeline.json").write_text(json.dumps(rows, indent=2))

        def clock(t):
            return f"{int(t // 60)}:{t % 60:04.1f}"

        md = ["| # | Time | Part | On-screen caption | Narration |", "|---|---|---|---|---|"]
        for r in rows:
            md.append(f"| {r['part']} | {clock(r['start'])}–{clock(r['end'])} | {r['label']} | "
                      f"{' / '.join(r['captions'])} | {r['narration']} |")
        (BUILD / "timeline.md").write_text("\n".join(md) + "\n")

        def stamp(t):
            ms = int(round(t * 1000))
            return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

        srt = [f"{k}\n{stamp(a)} --> {stamp(b)}\n{text}\n" for k, (a, b, text) in enumerate(cues, start=1)]
        (ROOT / "investor-demo.srt").write_text("\n".join(srt))
        return times[-1][1]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--edit", default=str(ROOT / "edit.json"))
    ap.add_argument("--out", default=str(ROOT / "investor-demo.mp4"))
    ap.add_argument("--only", help="comma-separated part numbers to re-render (others reused from build/)")
    ap.add_argument("--draft", action="store_true", help="fast, lower-quality encode")
    ap.add_argument("--stills", action="store_true", help="only draw cards and captions")
    ap.add_argument("--timeline", action="store_true", help="only write the timeline report and subtitles")
    ap.add_argument("--clean", action="store_true", help="delete build/ first")
    args = ap.parse_args()

    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise SystemExit(f"missing required tool: {tool}")
    if args.clean and BUILD.exists():
        shutil.rmtree(BUILD)
    edit = json.loads(Path(args.edit).read_text())
    missing = sorted({s["scene"] for s in edit["timeline"] if s["type"] == "clip"
                      and not ((REC / f"{s['scene']}.webm").exists() and (REC / f"{s['scene']}.json").exists())})
    if missing:
        raise SystemExit("missing recordings (run ./record_demo.sh): " + ", ".join(missing))

    b = Builder(edit, args.draft)
    only = {int(x) for x in args.only.split(",")} if args.only else None
    if args.timeline:
        b.render(only=set())  # nothing rendered; durations only
        total = b.write_timeline()
        print(f"timeline: {BUILD / 'timeline.md'}  ({total:.1f}s)   subtitles: {ROOT / 'investor-demo.srt'}")
        return
    print("rendering parts")
    b.render(only, args.stills)
    if args.stills:
        print(f"stills in {BUILD}/")
        return
    not_built = [i for i in range(len(edit["timeline"])) if not b.part_path(i).exists()]
    if not_built:
        raise SystemExit(f"parts not rendered yet: {not_built} (run without --only)")
    total = b.write_timeline()
    print(f"assembling {len(edit['timeline'])} parts → {Path(args.out).name}  ({total:.1f}s)")
    b.assemble(Path(args.out))
    probe = run(["ffprobe", "-v", "error", "-show_entries", "format=duration,size", "-of", "csv=p=0", args.out]).stdout.strip()
    dur, size = probe.split(",")
    print(f"done: {args.out}  {float(dur):.1f}s  {int(size) / 1e6:.1f} MB")
    print(f"timeline: {BUILD / 'timeline.md'}   subtitles: {ROOT / 'investor-demo.srt'}")


if __name__ == "__main__":
    main()
