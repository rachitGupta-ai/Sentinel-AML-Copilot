#!/usr/bin/env python3
"""Build the SentinelAML Copilot prototype submission deck (.pptx).

Dark navy theme + bright Snowflake-blue accents and full-bleed colored cards
(the preferred v1 look), kept on-brand by overlaying the organizers' official
frame (docs/assets/coco-frame.png) whose BODY IS TRANSPARENT: the navy background
shows through, while the black header bar (YourStory/Snowflake/H2S + "CoCo CLI
HACKATHON GCC Edition") and the cyan->blue bottom strip stay intact on every slide.
The closing slide reuses the organizers' "THANK YOU" art (coco-thankyou.png).

Grounded in docs/ (product-overview.md, product-design.md, DEMO.md, VIDEO_PLAN.md)
and the live stack (Snowflake acct VNB57096, DB GOVERNED_AML, Cortex
claude-sonnet-5-5 + snowflake-arctic-embed-m-v1.5). All data is synthetic.

Prereqs: docs/assets/{coco-frame.png, coco-thankyou.png, sentinelaml-architecture-dark.png}
         (run scripts/render_architecture.py first).
Usage:   python3 scripts/build_submission_deck.py
Output:  docs/SentinelAML-Copilot-Prototype-Submission.pptx
"""
from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Pt

# ----------------------------------------------------------------------------- paths
ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "docs" / "assets"
FRAME = ASSETS / "coco-frame.png"            # transparent body; opaque header + bottom strip
THANKYOU = ASSETS / "coco-thankyou.png"
ARCH = ASSETS / "sentinelaml-architecture-dark.png"
ARCH_LIGHT = ASSETS / "sentinelaml-architecture.png"  # fallback

# ----------------------------------------------------------------------------- theme (dark v1)
NAVY = RGBColor(0x0B, 0x1F, 0x3A)      # deep background
PANEL = RGBColor(0x14, 0x2A, 0x4A)     # card panel
PANEL2 = RGBColor(0x1C, 0x38, 0x5E)    # lighter card
SNOW = RGBColor(0x29, 0xB5, 0xE8)      # Snowflake blue (accent)
CYAN = RGBColor(0x5A, 0xD1, 0xF0)
GREEN = RGBColor(0x51, 0xCF, 0x66)
AMBER = RGBColor(0xFF, 0xD4, 0x3B)
RED = RGBColor(0xFF, 0x6B, 0x6B)
WHITE = RGBColor(0xF2, 0xF6, 0xFC)
MUTE = RGBColor(0xA9, 0xBD, 0xD6)
FONT = "Calibri"

# 16:9
EMU_W = Emu(12192000)
EMU_H = Emu(6858000)
# organizers' header occupies ~9.3% of height; keep content below it
HEADER_BOT = Emu(int(EMU_H.emu * 0.098))   # ~0.672 in


def _solid(shape, color):
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.fill.background()
    shape.shadow.inherit = False


def base(slide):
    """Dark navy background, then the organizers' transparent frame on top."""
    r = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, EMU_W, EMU_H)
    _solid(r, NAVY)
    pic = slide.shapes.add_picture(str(FRAME), 0, 0, EMU_W, EMU_H)
    # order: background rect first, frame above it, content above frame
    tree = slide.shapes._spTree
    tree.remove(r._element); tree.insert(2, r._element)
    tree.remove(pic._element); tree.insert(3, pic._element)
    return r


def tb(slide, x, y, w, h, lines, size=14, color=WHITE, bold=False,
       align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, space=4):
    box = slide.shapes.add_textbox(x, y, w, h)
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    first = True
    for ln in lines:
        text, s, c, b = ln if isinstance(ln, tuple) else (ln, size, color, bold)
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        p.alignment = align
        p.space_after = Pt(space)
        p.space_before = Pt(0)
        run = p.add_run()
        run.text = text
        run.font.size = Pt(s)
        run.font.bold = b
        run.font.color.rgb = c
        run.font.name = FONT
    return box


def card(slide, x, y, w, h, fill=PANEL, line=None, lw=1.25):
    c = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h)
    c.adjustments[0] = 0.06
    c.fill.solid()
    c.fill.fore_color.rgb = fill
    if line:
        c.line.color.rgb = line
        c.line.width = Pt(lw)
    else:
        c.line.fill.background()
    c.shadow.inherit = False
    return c


def tab(slide, x, y, w, h, color):
    t = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h)
    t.adjustments[0] = 0.5
    _solid(t, color)
    return t


def chip(slide, x, y, w, text, color=SNOW, textcolor=NAVY, h=Pt(26)):
    c = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h)
    c.adjustments[0] = 0.5
    _solid(c, color)
    tf = c.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = text
    r.font.size = Pt(11)
    r.font.bold = True
    r.font.color.rgb = textcolor
    r.font.name = FONT
    return c


def heading(slide, kicker, title):
    tb(slide, Emu(500000), Emu(820000), Emu(11200000), Emu(360000),
       [(kicker, 13, SNOW, True)])
    tb(slide, Emu(500000), Emu(1080000), Emu(11200000), Emu(640000),
       [(title, 28, WHITE, True)])


def footer(slide, n):
    tb(slide, Emu(500000), Emu(6430000), Emu(9000000), Emu(300000),
       [("SentinelAML Copilot · Challenge 1 — Risk, Fraud & Regulatory Intelligence Copilot", 9.5, MUTE, False)])
    tb(slide, Emu(11000000), Emu(6430000), Emu(900000), Emu(300000),
       [(str(n), 10, MUTE, True)], align=PP_ALIGN.RIGHT)


# ----------------------------------------------------------------------------- build
prs = Presentation()
prs.slide_width = EMU_W
prs.slide_height = EMU_H
BLANK = prs.slide_layouts[6]
ARCH_IMG = ARCH if ARCH.exists() else ARCH_LIGHT


def new():
    s = prs.slides.add_slide(BLANK)
    base(s)
    return s


# ===== Slide 1 — Title card (official template fields) =======================
s = new()
tb(s, Emu(600000), Emu(1650000), Emu(11000000), Emu(900000),
   [("SentinelAML Copilot", 52, WHITE, True)])
tb(s, Emu(620000), Emu(2520000), Emu(11000000), Emu(560000),
   [("Every figure governed. Every claim cited. Every decision auditable.", 20, SNOW, True)])
fw, fh = Emu(5450000), Emu(560000)
rows = [
    ("Problem Statement", "Challenge 1 — Risk, Fraud & Regulatory Intelligence Copilot"),
    ("Team Name", "<your team name>"),
    ("Team Leader Name", "Rachit Gupta"),
    ("Team Size", "<n>"),
]
for i, (k, v) in enumerate(rows):
    x = Emu(600000 + (i % 2) * 5700000)
    y = Emu(3450000 + (i // 2) * 700000)
    card(s, x, y, fw, fh, PANEL)
    tb(s, x + Emu(180000), y + Emu(70000), fw - Emu(300000), fh - Emu(120000),
       [(k, 11, MUTE, True), (v, 15, WHITE, True)], space=2)
tb(s, Emu(600000), Emu(5250000), Emu(11000000), Emu(500000),
   [("Built natively on the Snowflake AI Data Cloud with the CoCo CLI workflow · Snowflake Cortex · All data fully synthetic",
     12, MUTE, False)])

# ===== Slide 2 — Problem brief + stats =======================================
s = new()
heading(s, "PROBLEM BRIEF", "AML investigation is slow, inconsistent — and can't trust a chatbot")
lx, ly, lw = Emu(500000), Emu(1780000), Emu(6300000)
card(s, lx, ly, lw, Emu(4300000), PANEL)
tb(s, lx + Emu(260000), ly + Emu(200000), lw - Emu(500000), Emu(4000000),
   [
    ("The business problem", 15, SNOW, True),
    ("Risk, fraud & compliance teams at banks and NBFCs run AML investigations across fragmented systems and manual steps — pulling transactions, aggregating risk, cross-referencing policy, writing SARs, and assembling audit evidence.", 12.5, WHITE, False),
    ("", 5, WHITE, False),
    ("Two things make it slow and risky", 13, CYAN, True),
    ("•  Figures aren't trustworthy by default — the same question gives different answers across teams; definitions live in spreadsheets, not one governed place.", 12, WHITE, False),
    ("•  A generic LLM copilot makes it worse — it will invent a regulatory figure or an unsupported conclusion, unacceptable when output may reach a regulator.", 12, WHITE, False),
    ("", 5, WHITE, False),
    ("Target users", 13, CYAN, True),
    ("Fraud/AML analyst · Compliance officer · Auditor · Risk & operations leadership.", 12, WHITE, False),
    ("", 5, WHITE, False),
    ("Result today", 13, CYAN, True),
    ("Long investigation times, inconsistent figures, and AI output that cannot be defended in an audit.", 12, WHITE, False),
   ], space=5)
sx, sw = Emu(7050000), Emu(4650000)
stats = [
    ("USD 3.1 Tn", "estimated illicit funds flowing through the system each year (UNODC: ~2–5% of global GDP)", SNOW),
    ("< 1%", "of global laundered money is intercepted — the detection gap AML teams fight daily", RED),
    ("95%+", "of AML alerts are false positives at many institutions — analysts drown in manual triage", AMBER),
    ("Hours → case", "manual SAR investigations stitch transactions, policy & evidence by hand before filing", CYAN),
]
yy = Emu(1780000)
for big, small, col in stats:
    card(s, sx, yy, sw, Emu(1000000), PANEL2)
    tb(s, sx + Emu(240000), yy + Emu(110000), sw - Emu(400000), Emu(800000),
       [(big, 25, col, True), (small, 11.5, WHITE, False)], space=3)
    yy = Emu(yy + 1080000)
tb(s, sx, Emu(6150000), sw, Emu(300000),
   [("Industry figures are illustrative context, not measured product results.", 9.5, MUTE, False)])
footer(s, 2)

# ===== Slide 3 — How the product solves it ===================================
s = new()
heading(s, "THE SOLUTION", "Governed figures + cited narrative + immutable audit")
tb(s, Emu(500000), Emu(1720000), Emu(11200000), Emu(440000),
   [("The hard rule that makes it NOT a chatbot: the LLM never computes a regulatory number — it only narrates around numbers Snowflake already computed.",
     14, CYAN, True)])
pillars = [
    ("1 · Governed figures", "Every number comes from a versioned governed semantic view. Deterministic — same question, same value + version, for everyone. 'Same answer, provably.'", SNOW),
    ("2 · Dual-grounding gate", "Actionable only when every numeric claim cites a metric + lineage AND every textual claim cites a policy passage, above a 0.7 groundedness threshold. Else: refuse / flag.", GREEN),
    ("3 · Human authority", "No AI output becomes an action without a human approval gate. A risk SIGNAL never auto-becomes a confirmed risk EVENT.", AMBER),
    ("4 · Immutable audit", "The full chain — question → metric → query → rows → narrative → approval → outcome — is an append-only, replayable audit record.", CYAN),
]
cw, gap = Emu(2730000), Emu(200000)
x = Emu(500000)
for title, body, col in pillars:
    card(s, x, Emu(2300000), cw, Emu(2350000), PANEL)
    tab(s, x, Emu(2300000), cw, Pt(8), col)
    tb(s, x + Emu(200000), Emu(2450000), cw - Emu(380000), Emu(2150000),
       [(title, 15, col, True), ("", 4, WHITE, False), (body, 12, WHITE, False)], space=4)
    x = Emu(x.emu + cw.emu + gap.emu)
card(s, Emu(500000), Emu(4850000), Emu(11200000), Emu(1350000), PANEL2)
tb(s, Emu(720000), Emu(4960000), Emu(11000000), Emu(360000), [("What companies get", 15, SNOW, True)])
outs = [
    "Faster, defensible investigations — NL questions return pre-cited evidence in seconds.",
    "Consistency across departments — one governed definition ends 'same question, different answer'.",
    "Audit-ready by construction — full lineage from records alone; tamper attempts are themselves audited.",
    "Safe AI adoption — the copilot declines rather than fabricates, trustworthy in a regulated setting.",
]
tb(s, Emu(720000), Emu(5370000), Emu(11000000), Emu(800000),
   [("•  " + o, 12, WHITE, False) for o in outs], space=4)
footer(s, 3)

# ===== Slide 4 — Stages of the product =======================================
s = new()
heading(s, "PRODUCT STAGES", "One end-to-end golden path, seven governed stages")
stages = [
    ("INGEST", "Synthetic txns/alerts/policy land via Snowflake stream + task; dedup by event_id; freshness indicator.", SNOW),
    ("TRIAGE", "Alert → case RECEIVED; risk aggregated from governed metrics; alerts correlate to one case; labelled Risk_Signal.", CYAN),
    ("INVESTIGATE", "Analyst asks in NL; semantic interpretation shown first; governed figures + Cortex narrative, visibly distinct.", GREEN),
    ("GROUND", "Dual-grounding + groundedness score; actionable only if grounded & above threshold; else refuse / flag.", AMBER),
    ("DRAFT SAR", "Cortex drafts an audit-ready SAR; every figure → metric+version+lineage, every assertion → policy; decision-support.", SNOW),
    ("APPROVE", "Compliance officer reviews lineage + score → approve/reject; filing simulated only; idempotent (at most once).", CYAN),
    ("AUDIT + KPI", "Immutable record of every step; full lineage replay; KPIs with demonstrated vs intended values separated.", GREEN),
]
colw, gap = Emu(1540000), Emu(80000)
x0, y1 = Emu(500000), Emu(1820000)
for i, (name, body, col) in enumerate(stages):
    x = Emu(x0.emu + i * (colw.emu + gap.emu))
    chip(s, x, y1, colw, name, color=col, textcolor=NAVY)
    card(s, x, Emu(y1.emu + 320000), colw, Emu(2500000), PANEL)
    tb(s, x + Emu(120000), Emu(y1.emu + 420000), colw - Emu(230000), Emu(2350000),
       [(body, 10.5, WHITE, False)], space=3)
card(s, Emu(500000), Emu(5050000), Emu(11200000), Emu(1150000), PANEL2)
tb(s, Emu(720000), Emu(5140000), Emu(11000000), Emu(340000),
   [("Case lifecycle state machine", 14, SNOW, True)])
tb(s, Emu(720000), Emu(5500000), Emu(11000000), Emu(680000),
   [("RECEIVED → TRIAGED → INVESTIGATING → RECOMMENDATION_READY → AWAITING_APPROVAL → APPROVED / REJECTED → ACTION_COMPLETED / ACTION_FAILED → CLOSED",
     13, WHITE, True),
    ("Every transition emits an append-only audit record. A Risk_Signal becomes a Risk_Event only through the human approval gate.",
     11.5, MUTE, False)], space=5)
footer(s, 4)

# ===== Slide 5 — Architecture (embedded diagram) =============================
s = new()
heading(s, "ARCHITECTURE & DATA FLOW", "How the stages connect across Snowflake, Cortex & CoCo CLI")
AR = 2200 / 1300
img_y = Emu(1800000)
avail_h = Emu(6300000 - img_y.emu)
img_h = avail_h
img_w = Emu(int(img_h.emu * AR))
if img_w.emu > 11400000:
    img_w = Emu(11400000)
    img_h = Emu(int(img_w.emu / AR))
img_x = Emu((EMU_W.emu - img_w.emu) // 2)
s.shapes.add_picture(str(ARCH_IMG), img_x, img_y, img_w, img_h)
footer(s, 5)

# ===== Slide 6 — Golden-path sequence ========================================
s = new()
heading(s, "GOLDEN-PATH SEQUENCE", "Trace one case: alert → SAR → approval → audit")
steps = [
    ("Stream/Task", "Alert on a structuring pattern (ENT-SMURF-001); two alerts correlate to one case.", SNOW),
    ("Triage → Semantic View", "Aggregate governed metrics: structuring_score ≈ 0.9, exposure_90d, txn_velocity — each stamped version v1.", CYAN),
    ("Analyst (NL) → Investigation", "'Why is this entity high risk?' → semantic interpretation shown → canonical metrics resolved via glossary.", GREEN),
    ("Grounding → Cortex", "Assemble numeric + textual evidence → Cortex narrates → compute groundedness → dual-grounding check (else refuse/flag).", AMBER),
    ("SAR Service → Cortex", "Draft SAR from governed figures + cited policy (POL-SAR-BASIS-003) → labelled AI-generated decision support; not 'filed'.", SNOW),
    ("Compliance Officer", "Review lineage + score → Approve → APPROVED → ACTION_COMPLETED (filing simulated only, idempotent).", CYAN),
    ("Audit Service", "Replay full lineage from AUDIT.AUDIT_RECORD alone; KPIs update (grounded %, correlation count, dedup count).", GREEN),
]
y = Emu(1820000)
rowh = Emu(620000)
for i, (actor, body, col) in enumerate(steps):
    card(s, Emu(500000), y, Emu(3100000), Emu(540000), PANEL)
    tab(s, Emu(500000), y, Pt(7), Emu(540000), col)
    tb(s, Emu(680000), y + Emu(50000), Emu(2900000), Emu(450000),
       [(f"{i+1}. {actor}", 12.5, col, True)], anchor=MSO_ANCHOR.MIDDLE)
    card(s, Emu(3720000), y, Emu(7980000), Emu(540000), PANEL2)
    tb(s, Emu(3900000), y + Emu(40000), Emu(7700000), Emu(470000),
       [(body, 11.5, WHITE, False)], anchor=MSO_ANCHOR.MIDDLE)
    y = Emu(y.emu + rowh.emu)
footer(s, 6)

# ===== Slide 7 — Technical depth =============================================
s = new()
heading(s, "TECHNICAL DEPTH", "Snowflake-native, CoCo CLI-built, test-backed")
left = [
    ("Snowflake features (justified, not forced)", SNOW),
    ("Stream + Task — native ingestion, dedup by event_id, freshness; multi-replica safe (dedup server-side).", WHITE),
    ("Semantic View + Metric_Definition_Registry — single versioned source of every risk figure.", WHITE),
    ("Cortex — COMPLETE (narrative), EMBED_TEXT + VECTOR_COSINE_SIMILARITY (evidence retrieval).", WHITE),
    ("Masking + Row-access policies — role-based protection of sensitive synthetic fields.", WHITE),
    ("Append-only AUDIT table — INSERT/SELECT grants only; UPDATE/DELETE denied → immutable lineage.", WHITE),
    ("CoCo CLI — provisions all objects + seeds a clean account reproducibly.", WHITE),
]
card(s, Emu(500000), Emu(1780000), Emu(5700000), Emu(2700000), PANEL)
tb(s, Emu(700000), Emu(1890000), Emu(5350000), Emu(2500000),
   [(left[0][0], 14, left[0][1], True)] +
   [("•  " + t, 11.5, c, False) for t, c in left[1:]], space=5)
card(s, Emu(6300000), Emu(1780000), Emu(5400000), Emu(2700000), PANEL)
tb(s, Emu(6500000), Emu(1890000), Emu(5050000), Emu(2500000),
   [("Live stack & engineering guarantees", 14, SNOW, True),
    ("Backend: Python 3.11 + FastAPI (interface + Impl); config-only credentials with a startup guard — no secrets in source/prompts/logs.", 11.5, WHITE, False),
    ("Account VNB57096 · DB GOVERNED_AML · Cortex claude-sonnet-5-5 + snowflake-arctic-embed-m-v1.5.", 11.5, CYAN, True),
    ("Determinism: same (entity, metric, data state) → identical value + version for any role.", 11.5, WHITE, False),
    ("Graceful degradation: Cortex timeout → bounded retry → fail safe (no ungrounded output) → human review.", 11.5, WHITE, False),
    ("Property-based tests (hypothesis, ≥100 iters) on the correctness invariants.", 11.5, WHITE, False),
   ], space=6)
card(s, Emu(500000), Emu(4620000), Emu(11200000), Emu(1500000), PANEL2, line=GREEN, lw=1.5)
tb(s, Emu(720000), Emu(4710000), Emu(11000000), Emu(340000),
   [("Evaluation harness — DEMONSTRATED results (synthetic data, offline)", 13, GREEN, True)])
evals = [
    ("Groundedness 1.0", "Citation precision 1.0 (3/3)", "Citation coverage 1.0 (3/3)"),
    ("Unsupported-claim rate 0.0 (0/6)", "Refusal correctness 1.0 (6/6)", "Metric consistency 1.0 (2/2)"),
    ("Injection resistance 1.0 (2/2) — doc rejected, figure unchanged", "", ""),
]
for col in range(3):
    tb(s, Emu(720000 + col * 3700000), Emu(5100000), Emu(3550000), Emu(850000),
       [(evals[r][col], 11.5, WHITE, False) for r in range(3) if evals[r][col]], space=5)
tb(s, Emu(720000), Emu(5880000), Emu(11000000), Emu(220000),
   [("Demonstrated values shown separately from intended production targets; never reported before tests run.", 9.5, MUTE, False)])
footer(s, 7)

# ===== Slide 8 — Impact & scalability ========================================
s = new()
heading(s, "IMPACT & SCALABILITY", "Measurable outcomes, and where it goes beyond the demo")
tiles = [
    ("Investigation time", "Hours of manual stitching → a cited answer in seconds; SAR drafted with full lineage.", SNOW),
    ("Consistency", "'Same answer, provably' — two roles, identical governed value + version. Ends cross-team disputes.", CYAN),
    ("Trust & safety", "Dual-grounding + refusal: 0 unsupported claims and injection-resistant in evaluation.", GREEN),
    ("Audit readiness", "100% of steps in an immutable, replayable trail — audit prep becomes a replay, not a scramble.", AMBER),
]
cw, gap = Emu(2730000), Emu(200000)
x = Emu(500000)
for title, body, col in tiles:
    card(s, x, Emu(1780000), cw, Emu(1900000), PANEL)
    tab(s, x, Emu(1780000), cw, Pt(8), col)
    tb(s, x + Emu(190000), Emu(1930000), cw - Emu(360000), Emu(1700000),
       [(title, 14, col, True), ("", 3, WHITE, False), (body, 11.5, WHITE, False)], space=3)
    x = Emu(x.emu + cw.emu + gap.emu)
card(s, Emu(500000), Emu(3880000), Emu(5700000), Emu(2320000), PANEL2)
tb(s, Emu(700000), Emu(3980000), Emu(5350000), Emu(2150000),
   [("Scalability", 14, SNOW, True),
    ("•  Compute scales with Snowflake warehouses; dedup/freshness enforced server-side → multi-replica safe.", 12, WHITE, False),
    ("•  Governed-metric fabric is reusable: new metrics go in the registry, not in prompts.", 12, WHITE, False),
    ("•  Retrieval scales via Cortex embeddings over policy/evidence stores.", 12, WHITE, False),
    ("•  RBAC + masking/row-access policies make it enterprise-tenantable.", 12, WHITE, False),
   ], space=5)
card(s, Emu(6300000), Emu(3880000), Emu(5400000), Emu(2320000), PANEL2)
tb(s, Emu(6500000), Emu(3980000), Emu(5050000), Emu(2150000),
   [("Beyond the demo", 14, SNOW, True),
    ("•  Same fabric re-skins to adjacent domains: fraud, credit & liquidity risk, Basel reporting.", 12, WHITE, False),
    ("•  Feedback-driven threshold tuning; richer entity resolution.", 12, WHITE, False),
    ("•  Plug in real (governed) sources behind the same semantic layer — the governance contract is unchanged.", 12, WHITE, False),
    ("•  Rubric fit: Technical Execution 40% · Real-World Relevance 30% · Solution Completeness 30%.", 12, CYAN, True),
   ], space=5)
footer(s, 8)

# ===== Slide 9 — Thank you (organizers' art) =================================
s = prs.slides.add_slide(BLANK)
pic = s.shapes.add_picture(str(THANKYOU), 0, 0, EMU_W, EMU_H)
tree = s.shapes._spTree
tree.remove(pic._element); tree.insert(2, pic._element)
tb(s, Emu(600000), Emu(5950000), Emu(11000000), Emu(500000),
   [("SentinelAML Copilot — Every figure governed. Every claim cited. Every decision auditable.", 15, WHITE, True)],
   align=PP_ALIGN.CENTER)

# ----------------------------------------------------------------------------- save
out = ROOT / "docs" / "SentinelAML-Copilot-Prototype-Submission.pptx"
prs.save(str(out))
print(f"Saved {out}  ({len(prs.slides._sldIdLst)} slides)")
