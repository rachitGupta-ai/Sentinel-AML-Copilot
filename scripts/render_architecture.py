#!/usr/bin/env python3
"""Render the SentinelAML Copilot architecture diagram as crisp PNGs (Pillow only).

Produces BOTH:
  docs/assets/sentinelaml-architecture.png       (light theme — on white)
  docs/assets/sentinelaml-architecture-dark.png  (dark theme — on navy deck)

Run with no args to build both. The deck uses the dark one.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ---- theme selection --------------------------------------------------------
DARK = "--dark" in sys.argv

if DARK:
    BG = (11, 31, 58, 255)           # navy (matches deck NAVY 0B1F3A)
    INK = (242, 246, 252)            # near-white text
    SNOW = (41, 181, 232)
    SNOW_D = (90, 209, 240)
    CYAN = (90, 209, 240)
    GREEN = (81, 207, 102)
    AMBER = (255, 212, 59)
    AMBER_FILL = (58, 60, 30)
    PANEL = (20, 42, 74)
    PANEL_LINE = (54, 86, 128)
    SNOW_FILL = (23, 56, 94)
    GREEN_FILL = (24, 58, 44)
    MUTE = (169, 189, 214)
    CARD = (28, 56, 94)              # inner object cards
    CARD_TEXT = (242, 246, 252)
else:
    BG = (255, 255, 255, 0)          # transparent (frame provides white)
    INK = (11, 31, 58)
    SNOW = (41, 141, 232)
    SNOW_D = (12, 90, 160)
    CYAN = (36, 203, 229)
    GREEN = (46, 160, 86)
    AMBER = (209, 150, 0)
    AMBER_FILL = (255, 243, 205)
    PANEL = (238, 245, 252)
    PANEL_LINE = (183, 208, 232)
    SNOW_FILL = (224, 240, 251)
    GREEN_FILL = (226, 244, 232)
    MUTE = (110, 134, 164)
    CARD = (255, 255, 255)
    CARD_TEXT = (11, 31, 58)

W, H = 2200, 1300
SCALE = 2  # supersample for crisp text
img = Image.new("RGBA", (W * SCALE, H * SCALE), BG)
d = ImageDraw.Draw(img)


def font(sz, bold=False):
    paths = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for p in paths:
        try:
            return ImageFont.truetype(p, sz * SCALE)
        except OSError:
            continue
    return ImageFont.load_default()


def rrect(x, y, w, h, r, fill=None, outline=None, width=2):
    d.rounded_rectangle([x * SCALE, y * SCALE, (x + w) * SCALE, (y + h) * SCALE],
                        radius=r * SCALE, fill=fill, outline=outline, width=width * SCALE)


def text(x, y, s, sz, color=INK, bold=False, center=False, maxw=None):
    f = font(sz, bold)
    if center and maxw:
        tw = d.textlength(s, font=f) / SCALE
        x = x + (maxw - tw) / 2
    d.text((x * SCALE, y * SCALE), s, font=f, fill=color)


def textw(s, sz, bold=False):
    return d.textlength(s, font=font(sz, bold)) / SCALE


def band(x, y, w, h, title, items, accent, fill, line):
    rrect(x, y, w, h, 14, fill=fill, outline=line, width=2)
    rrect(x, y, 10, h, 5, fill=accent)
    text(x + 26, y + 14, title, 24, color=accent, bold=True)
    text(x + 26, y + 50, items, 18, color=INK)


# ---- title ------------------------------------------------------------------
text(40, 24, "SentinelAML Copilot — Architecture & Data Flow", 34, color=INK, bold=True)
text(42, 74, "Governed figures · dual-grounded narrative · immutable audit — native on Snowflake, built with the CoCo CLI",
     19, color=MUTE)

# ---- layered bands ----------------------------------------------------------
LX, LW = 40, 2120
band(LX, 130, LW, 92, "EXPERIENCE · Next.js",
     "Command Centre · Investigation workspace · Evidence / citation drawer · Approval queue · Audit trail · Health & AI-quality",
     CYAN, PANEL, PANEL_LINE)
band(LX, 240, LW, 92, "APPLICATION · FastAPI",
     "Routers: ingest / cases / investigate / sar / approval / audit / kpi   ·   RBAC + purpose check   ·   WebSocket live updates",
     SNOW, PANEL, PANEL_LINE)
band(LX, 350, LW, 110, "SERVICES  (interface + Impl)",
     "Triage · Governed-Metric (semantic-view only) · NL Investigation · Dual-Grounding scorer · SAR · Approval / HITL · Guard · Audit · KPI",
     GREEN, PANEL, PANEL_LINE)

# flow arrows between layers
def vchevron(cx, y, color=SNOW):
    s = 11
    d.polygon([((cx - s) * SCALE, y * SCALE), ((cx + s) * SCALE, y * SCALE),
               (cx * SCALE, (y + s) * SCALE)], fill=color)

for cx in (300, 1100, 1900):
    vchevron(cx, 226, SNOW_D)
    vchevron(cx, 336, GREEN)

# ---- trust boundary (guard) -------------------------------------------------
TBY = 480
rrect(LX, TBY, LW, 86, 14, fill=AMBER_FILL, outline=AMBER, width=2)
text(LX + 26, TBY + 12, "TRUST BOUNDARY", 20, color=AMBER, bold=True)
text(LX + 26, TBY + 44, "Prompt-injection detector  →  generated-query allow-list validator   ·   untrusted input (documents + NL) cleared BEFORE the governed zone",
     17, color=INK)
for cx in (300, 1100, 1900):
    vchevron(cx, 570, AMBER)

# ---- Snowflake zone ---------------------------------------------------------
SZY = 590
rrect(LX, SZY, LW, 300, 16, fill=SNOW_FILL, outline=SNOW, width=3)
text(LX + 28, SZY + 16, "SNOWFLAKE AI DATA CLOUD", 24, color=SNOW_D, bold=True)
text(LX + 420, SZY + 20, "(the governed zone — Cortex is given numbers, it never produces them)", 16, color=MUTE)

# inner object cards
def obj(x, y, w, title, body, accent):
    rrect(x, y, w, 150, 12, fill=CARD, outline=PANEL_LINE, width=2)
    rrect(x, y, w, 8, 4, fill=accent)
    text(x + 18, y + 18, title, 19, color=accent, bold=True)
    text(x + 18, y + 50, body, 15, color=CARD_TEXT)

oy = SZY + 64
ow = (LW - 28 * 2 - 20 * 3) / 4
cols = [LX + 28 + i * (ow + 20) for i in range(4)]
obj(cols[0], oy, ow, "RAW + Stream/Task",
    "transaction_event · alert ·\nentity · policy_doc\nIngest · dedup (event_id) ·\nfreshness", SNOW_D)
obj(cols[1], oy, ow, "SEM — governed",
    "Semantic_View +\nMetric_Definition_Registry\n(versioned — the ONLY\nsource of figures)", GREEN)
obj(cols[2], oy, ow, "Cortex + VEC",
    "COMPLETE (narrative)\nEMBED_TEXT ·\nVECTOR_COSINE_SIMILARITY\nover evidence stores", SNOW)
obj(cols[3], oy, ow, "AUDIT + Policies",
    "Append-only AUDIT_RECORD\n(INSERT/SELECT only)\nMasking + Row-access\npolicies (RBAC)", AMBER)

# ---- bottom: golden-path pipeline ------------------------------------------
PY = 930
text(LX, PY, "Golden path", 22, color=INK, bold=True)
stages = [
    ("INGEST", SNOW_D), ("TRIAGE", CYAN), ("INVESTIGATE", GREEN), ("GROUND", AMBER),
    ("DRAFT SAR", SNOW), ("APPROVE", CYAN), ("AUDIT + KPI", GREEN),
]
py2 = PY + 40
gap = 16
sw = (LW - gap * (len(stages) - 1)) / len(stages)
x = LX
for i, (name, col) in enumerate(stages):
    rrect(x, py2, sw, 60, 30, fill=col)
    text(x, py2 + 17, name, 18, color=(255, 255, 255), bold=True, center=True, maxw=sw)
    if i < len(stages) - 1:
        ax = x + sw + 1
        d.polygon([(ax * SCALE, (py2 + 18) * SCALE), (ax * SCALE, (py2 + 42) * SCALE),
                   ((ax + gap - 2) * SCALE, (py2 + 30) * SCALE)], fill=MUTE)
    x += sw + gap

# state machine line
text(LX, py2 + 86, "RECEIVED → TRIAGED → INVESTIGATING → RECOMMENDATION_READY → AWAITING_APPROVAL → APPROVED/REJECTED → ACTION_COMPLETED → CLOSED",
     16, color=MUTE, bold=True)
text(LX, py2 + 116, "Every transition emits an append-only audit record · a Risk_Signal becomes a Risk_Event only through the human approval gate",
     15, color=MUTE)

# ---- downscale & save -------------------------------------------------------
out_dir = Path(__file__).resolve().parents[1] / "docs" / "assets"
out_dir.mkdir(parents=True, exist_ok=True)
name = "sentinelaml-architecture-dark.png" if DARK else "sentinelaml-architecture.png"
out = out_dir / name
final = img.resize((W, H), Image.LANCZOS)
final.save(str(out))
print(f"Saved {out}  ({W}x{H}, {'dark' if DARK else 'light'})")
