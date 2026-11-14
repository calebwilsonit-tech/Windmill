"""Design engine: a unique, professional, accessible visual identity per project, plus the UI rules
and critic checklist the agent works against. Every palette is contrast-checked (WCAG)."""
import colorsys
import json
import random
import re
from pathlib import Path

# ------------------------------------------------------------------ art directions
DIRECTIONS = {
    "editorial": dict(
        label="Editorial / magazine", mode="light", hues="mono+accent", sat=(0.55, 0.8), neutral_tint=0.04,
        fonts=[("Fraunces", "Inter"), ("Instrument Serif", "Instrument Sans"), ("Newsreader", "Geist"),
               ("Playfair Display", "Source Sans 3")],
        scale=1.414, radius=2, shadow="none - use hairline rules (1px) and whitespace for separation",
        motion="slow, subtle fades (250-400ms); no bouncing",
        moves=["Oversized serif headlines with tight leading (1.0-1.1) next to small, wide-tracked uppercase labels",
               "Asymmetric 12-col grid: content in cols 1-7, pull-quotes / meta in 9-12",
               "Thin horizontal rules and numbered sections (01, 02, 03) instead of boxes and cards",
               "One ink-colour accent used sparingly for links and drop caps"],
        avoid=["card grids with shadows", "gradient blobs", "rounded pill buttons everywhere"]),
    "swiss": dict(
        label="Swiss / International style", mode="light", hues="primary", sat=(0.75, 0.95), neutral_tint=0.0,
        fonts=[("Archivo", "Archivo"), ("Inter Tight", "Inter"), ("Space Grotesk", "IBM Plex Sans")],
        scale=1.5, radius=0, shadow="none", motion="instant or very fast (120ms); purposeful",
        moves=["Huge, bold, flush-left type set on a strict visible grid",
               "One saturated primary (red/blue/yellow) as large flat colour fields",
               "Numbers and data set large as graphic elements", "Generous margins; strong alignment lines"],
        avoid=["centered everything", "drop shadows", "decorative gradients"]),
    "brutalist": dict(
        label="Neo-brutalist", mode="light", hues="triadic", sat=(0.8, 1.0), neutral_tint=0.0,
        fonts=[("Space Grotesk", "Space Mono"), ("Archivo Black", "Work Sans"), ("Syne", "IBM Plex Mono")],
        scale=1.333, radius=0, shadow="hard offset: 4px 4px 0 #000 (no blur)", motion="snappy 100ms, translate on press",
        moves=["Thick 2-3px black borders on every surface", "Hard offset shadows that collapse when pressed",
               "Clashing flat colour blocks per section", "Raw, honest layout: visible structure, stickers, tags"],
        avoid=["soft shadows", "subtle greys", "glassmorphism"]),
    "soft-modern": dict(
        label="Soft modern product", mode="light", hues="analogous", sat=(0.55, 0.8), neutral_tint=0.06,
        fonts=[("Plus Jakarta Sans", "Plus Jakarta Sans"), ("Manrope", "Inter"), ("Figtree", "Figtree"), ("Onest", "Onest")],
        scale=1.25, radius=14, shadow="layered and soft: 0 1px 2px rgb(0 0 0/.06), 0 8px 24px rgb(0 0 0/.06)",
        motion="200ms ease-out, gentle lift on hover",
        moves=["Neutrals tinted with the brand hue (never pure grey)", "Bento-style asymmetric tiles of different sizes",
               "Product UI shown in context (real screenshots/mockups) instead of abstract art",
               "Micro-interactions: focus rings, optimistic updates, skeletons"],
        avoid=["the default centered hero + 3 identical feature cards", "purple-to-blue gradient blobs"]),
    "luxury": dict(
        label="Luxury / premium", mode="dark", hues="mono+accent", sat=(0.35, 0.55), neutral_tint=0.03,
        fonts=[("Cormorant Garamond", "Manrope"), ("Playfair Display", "Inter"), ("EB Garamond", "Figtree")],
        scale=1.5, radius=0, shadow="none; use light and contrast", motion="slow and smooth (400-700ms), parallax-light",
        moves=["Near-black backgrounds with warm off-white text and a metallic (brass/champagne) accent",
               "Very large display type, very small labels, lots of negative space",
               "Full-bleed photography / imagery with restrained overlays", "Thin 1px gold dividers"],
        avoid=["bright saturated colours", "busy layouts", "emoji / cartoon icons"]),
    "playful": dict(
        label="Playful / friendly", mode="light", hues="triadic", sat=(0.6, 0.85), neutral_tint=0.05,
        fonts=[("Fredoka", "Nunito"), ("Baloo 2", "Rubik"), ("Bricolage Grotesque", "Figtree")],
        scale=1.25, radius=22, shadow="soft coloured shadows tinted with the primary",
        motion="springy (overshoot) 250-350ms; celebrate success moments",
        moves=["Rounded, chunky shapes and blob/wave dividers", "Illustrated empty states and mascots/icons",
               "Bright section colours that change as you scroll", "Confetti / bounce on achievements"],
        avoid=["corporate stock photos", "grey-on-grey"]),
    "technical": dict(
        label="Technical / developer", mode="dark", hues="mono+accent", sat=(0.7, 0.95), neutral_tint=0.05,
        fonts=[("JetBrains Mono", "Inter"), ("IBM Plex Mono", "IBM Plex Sans"), ("Geist Mono", "Geist")],
        scale=1.2, radius=6, shadow="none; 1px borders and subtle inner glow", motion="fast 120-160ms",
        moves=["Monospace labels and numbers, tabular figures", "Visible grid lines / dot grid background",
               "Keyboard shortcuts shown in UI (kbd chips)", "Dense, scannable data tables with status dots"],
        avoid=["large decorative illustrations", "rounded bubbly shapes"]),
    "organic": dict(
        label="Organic / natural", mode="light", hues="analogous-warm", sat=(0.3, 0.55), neutral_tint=0.08,
        fonts=[("Fraunces", "Work Sans"), ("Libre Baskerville", "Lexend"), ("Literata", "DM Sans")],
        scale=1.333, radius=10, shadow="very soft, warm-tinted", motion="calm 300ms ease-in-out",
        moves=["Earthy palette (clay, moss, sand) with paper-like warm backgrounds",
               "Irregular organic shapes and hand-drawn accents", "Textured backgrounds (subtle grain via CSS)",
               "Generous imagery of real materials / people"],
        avoid=["neon", "cold blue-greys", "hard geometric everything"]),
    "retro-future": dict(
        label="Retro-futurism", mode="dark", hues="complementary", sat=(0.8, 1.0), neutral_tint=0.1,
        fonts=[("Unbounded", "Sora"), ("Orbitron", "Rajdhani"), ("Syne", "Outfit")],
        scale=1.333, radius=8, shadow="neon glow: 0 0 24px primary at 40%", motion="smooth 300ms; scanline/flicker accents",
        moves=["Duotone gradients (sunset magenta to cyan) on deep navy", "Chrome / outline display type",
               "Perspective grid horizons and glow", "Pill-shaped tags with glowing borders"],
        avoid=["flat corporate blue", "serif body text"]),
    "minimal-mono": dict(
        label="Minimal monochrome", mode="light", hues="mono+accent", sat=(0.8, 1.0), neutral_tint=0.0,
        fonts=[("Inter", "Inter"), ("Geist", "Geist"), ("Instrument Sans", "Instrument Sans")],
        scale=1.25, radius=4, shadow="none", motion="150ms opacity only",
        moves=["Pure black/white system with exactly ONE vivid accent colour",
               "Typography carries all hierarchy (weight + size), no boxes",
               "Huge whitespace, single-column focus", "Underlines and arrows (→) as the only ornaments"],
        avoid=["more than one accent", "icons everywhere", "decorative shadows"]),
    "glass-depth": dict(
        label="Glass & depth (restrained)", mode="dark", hues="analogous", sat=(0.7, 0.95), neutral_tint=0.08,
        fonts=[("Sora", "Inter"), ("Outfit", "Outfit"), ("Manrope", "Manrope")],
        scale=1.25, radius=18, shadow="large soft shadows + 1px inner highlight border (rgb(255 255 255/.12))",
        motion="250ms; subtle parallax on pointer move",
        moves=["Vivid blurred colour field behind translucent panels (backdrop-filter: blur)",
               "Layered depth: 3 elevation levels used consistently", "Light 1px top highlights on panels",
               "Content stays high-contrast despite translucency"],
        avoid=["glass on top of busy images", "low-contrast text on glass"]),
    "trustworthy": dict(
        label="Trustworthy / professional services", mode="light", hues="analogous-cool", sat=(0.45, 0.7), neutral_tint=0.04,
        fonts=[("Source Serif 4", "Source Sans 3"), ("Libre Franklin", "Libre Franklin"), ("IBM Plex Sans", "IBM Plex Sans")],
        scale=1.25, radius=8, shadow="subtle, 1-2 levels only", motion="restrained 200ms",
        moves=["Calm blue/green palette with very high legibility", "Clear trust signals: real names, numbers, credentials",
               "Plain-language headings that state benefits", "Accessible forms with inline validation"],
        avoid=["hype gradients", "tiny low-contrast text", "dark patterns"]),
    "game-fantasy": dict(
        label="Game UI: fantasy RPG", mode="dark", hues="analogous-warm", sat=(0.45, 0.75), neutral_tint=0.12,
        fonts=[("Cinzel", "Alegreya Sans"), ("Cormorant Garamond", "Alegreya Sans"), ("MedievalSharp", "Lato")],
        scale=1.333, radius=4, shadow="inner bevels + dark drop shadow", motion="weighty 250ms; sparkle on rare items",
        moves=["Framed panels (stone/wood/parchment via 9-slice or layered CSS borders)",
               "Rarity colours (common/uncommon/rare/epic/legendary) that are ALSO shown by icon/border shape",
               "Inventory grid with stack counts bottom-right, durability bars, hover tooltips with stat diffs",
               "Gold/brass accents for interactive elements"],
        avoid=["flat web-app styling", "tiny unreadable text over busy art"]),
    "game-scifi": dict(
        label="Game UI: sci-fi HUD", mode="dark", hues="complementary", sat=(0.75, 1.0), neutral_tint=0.1,
        fonts=[("Chakra Petch", "Rajdhani"), ("Orbitron", "Exo 2"), ("Oxanium", "Titillium Web")],
        scale=1.25, radius=0, shadow="glow on active elements", motion="fast 120ms + scan/boot-up sequences",
        moves=["Angular panels with cut corners (clip-path) and corner brackets",
               "Cyan primary + amber warning accents on near-black", "Segmented bars for health/shield/energy",
               "Diegetic touches: scanlines, tick marks, data readouts"],
        avoid=["rounded friendly shapes", "serif fonts"]),
    "game-pixel": dict(
        label="Game UI: pixel / retro", mode="dark", hues="triadic", sat=(0.7, 0.95), neutral_tint=0.1,
        fonts=[("Press Start 2P", "VT323"), ("Silkscreen", "Pixelify Sans"), ("Pixelify Sans", "VT323")],
        scale=2.0, radius=0, shadow="1-2px solid offset in a darker shade", motion="frame-stepped (steps()) animation",
        moves=["Limited 16-colour palette, no gradients, no anti-aliasing (image-rendering: pixelated)",
               "Chunky 2px borders with notched corners", "Integer scaling only (2x/3x/4x)",
               "Blinking cursor / selection arrows for menus"],
        avoid=["smooth gradients", "non-pixel fonts mixed in", "fractional scaling"]),
    "game-cozy": dict(
        label="Game UI: cozy", mode="light", hues="analogous-warm", sat=(0.35, 0.6), neutral_tint=0.1,
        fonts=[("Fredoka", "Nunito"), ("Baloo 2", "Quicksand"), ("Patrick Hand", "Nunito")],
        scale=1.25, radius=16, shadow="soft warm shadows", motion="bouncy 300ms; gentle idle wobble",
        moves=["Warm pastels on cream, hand-drawn outlines", "Big rounded buttons with icons + labels",
               "Inventory as a backpack/basket with slots that wiggle when full",
               "Friendly notifications that slide in like paper notes"],
        avoid=["harsh contrast", "aggressive reds", "dense data"]),
}


# ------------------------------------------------------------------ colour maths
def hsl(h, s, l):
    r, g, b = colorsys.hls_to_rgb((h % 360) / 360, max(0, min(1, l)), max(0, min(1, s)))
    return "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))


def to_hsl(hex_):
    h = hex_.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    hh, l, s = colorsys.rgb_to_hls(r, g, b)
    return hh * 360, s, l


def luminance(hex_):
    h = hex_.lstrip("#")
    def ch(c):
        c = int(c, 16) / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = ch(h[0:2]), ch(h[2:4]), ch(h[4:6])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def ensure_contrast(fg, bg, target):
    """Darken/lighten fg (keeping hue) until it reaches the target contrast against bg."""
    h, s, l = to_hsl(fg)
    go_dark = luminance(bg) > 0.4
    for _ in range(100):
        if contrast(fg, bg) >= target:
            return fg
        l = l - 0.01 if go_dark else l + 0.01
        if l <= 0 or l >= 1:
            break
        fg = hsl(h, s, l)
    return "#000000" if go_dark else "#ffffff"


def palette(d, rng, brand=None):
    base_h = to_hsl(brand)[0] if brand else rng.uniform(0, 360)
    s_lo, s_hi = d["sat"]
    sat = rng.uniform(s_lo, s_hi)
    strat = d["hues"]
    offsets = {"mono+accent": [0, 0], "primary": [0, 0], "analogous": [0, rng.choice([-35, 35])],
               "analogous-warm": [0, 30], "analogous-cool": [0, -30], "complementary": [0, 180],
               "triadic": [0, 120]}[strat]
    if strat == "analogous-warm":
        base_h = rng.uniform(15, 50) if not brand else base_h
    if strat == "analogous-cool":
        base_h = rng.uniform(190, 230) if not brand else base_h
    p_h, a_h = base_h + offsets[0], base_h + offsets[1]
    t = d["neutral_tint"]
    if d["mode"] == "light":
        bg = hsl(p_h, t * 3, rng.uniform(0.965, 0.99))
        surface = hsl(p_h, t * 3, 1.0 if rng.random() < 0.5 else 0.985)
        surface2 = hsl(p_h, t * 4, 0.93)
        border = hsl(p_h, t * 3, 0.85)
        text = ensure_contrast(hsl(p_h, t * 4, 0.12), bg, 12)
    else:
        bg = hsl(p_h, t * 3 + 0.05, rng.uniform(0.04, 0.08))
        surface = hsl(p_h, t * 3 + 0.05, 0.11)
        surface2 = hsl(p_h, t * 3 + 0.05, 0.16)
        border = hsl(p_h, t * 3, 0.24)
        text = ensure_contrast(hsl(p_h, t * 2, 0.93), bg, 12)
    muted = ensure_contrast(hsl(p_h, t * 3 + 0.05, 0.45 if d["mode"] == "light" else 0.65), bg, 4.6)
    primary = ensure_contrast(hsl(p_h, sat, rng.uniform(0.42, 0.55)), bg, 3.2)
    accent = ensure_contrast(hsl(a_h, min(1, sat + 0.05), 0.55 if strat != "mono+accent" else 0.5), bg, 3.0)
    on_primary = "#ffffff" if contrast("#ffffff", primary) >= contrast("#111111", primary) else "#111111"
    if contrast(on_primary, primary) < 4.5:
        primary = ensure_contrast(primary, on_primary, 4.5)
    pal = {"bg": bg, "surface": surface, "surface-2": surface2, "border": border, "text": text,
           "text-muted": muted, "primary": primary, "on-primary": on_primary, "accent": accent,
           "success": ensure_contrast(hsl(145, 0.6, 0.4), bg, 3.2),
           "warning": ensure_contrast(hsl(38, 0.9, 0.5), bg, 3.0),
           "danger": ensure_contrast(hsl(355, 0.75, 0.5), bg, 3.5)}
    if d is DIRECTIONS.get("game-fantasy") or d.get("label", "").startswith("Game UI: fantasy"):
        pal.update({"rarity-common": "#b8b8b8", "rarity-uncommon": "#3fbf5f", "rarity-rare": "#3f8fff",
                    "rarity-epic": "#b14fff", "rarity-legendary": "#ff9f1a"})
    return pal


def contrast_report(p):
    return {"text/bg": round(contrast(p["text"], p["bg"]), 1), "muted/bg": round(contrast(p["text-muted"], p["bg"]), 1),
            "primary/bg": round(contrast(p["primary"], p["bg"]), 1),
            "on-primary/primary": round(contrast(p["on-primary"], p["primary"]), 1)}


# ------------------------------------------------------------------ briefs
def make_brief(project, direction=None, seed=None, brand=None):
    rng = random.Random(seed if seed is not None else random.random())
    key = direction or rng.choice(list(DIRECTIONS))
    d = DIRECTIONS[key]
    display, body = rng.choice(d["fonts"])
    pal = palette(d, rng, brand)
    base = 16
    scale = [round(base * d["scale"] ** i) for i in range(-1, 6)]
    return {"project": project, "direction": key, "label": d["label"], "mode": d["mode"], "palette": pal,
            "contrast": contrast_report(pal), "fonts": {"display": display, "body": body},
            "type_scale_px": {"small": scale[0], "body": scale[1], "h4": scale[2], "h3": scale[3], "h2": scale[4],
                              "h1": scale[5], "display": scale[6]},
            "spacing_px": [4, 8, 12, 16, 24, 32, 48, 64, 96, 128], "radius_px": d["radius"],
            "shadow": d["shadow"], "motion": d["motion"], "signature_moves": rng.sample(d["moves"], min(3, len(d["moves"]))),
            "avoid": d["avoid"], "seed": seed}


# words in an idea that suit each direction (the look picker offers the best fits first, plus a wildcard)
AFFINITY = {
    "retro-future": "neon synthwave retro 80s 90s cyber cyberpunk arcade racing race car cars drift street tuning jdm "
                    "garage motorsport night glow vaporwave",
    "glass-depth": "sleek futuristic modern glass widget dashboard sharp premium tech wow slick",
    "technical": "developer dev code coding programmer engineer portfolio analytics data stats dashboard terminal "
                 "open-source opensource github api tool",
    "luxury": "luxury premium elegant high-end exclusive fashion jewelry watch boutique",
    "editorial": "blog magazine writing writer news story stories journal articles publication",
    "swiss": "design studio agency architecture architect typography poster",
    "brutalist": "bold raw zine indie experimental art artist band music underground",
    "soft-modern": "app saas startup product platform software subscription",
    "playful": "kids children fun playful cute family toy party colourful colorful",
    "organic": "natural organic bakery food cafe coffee garden plant eco wellness yoga farm",
    "minimal-mono": "minimal minimalist clean simple photography photographer gallery black white",
    "trustworthy": "business finance bank law lawyer clinic doctor health insurance consulting professional services",
    "game-scifi": "sci-fi scifi space hud mech cyber futuristic shooter",
    "game-fantasy": "fantasy rpg medieval dungeon magic quest",
    "game-pixel": "pixel retro 8-bit 16-bit arcade platformer",
    "game-cozy": "cozy farming life-sim cute relaxing",
}


def rank_directions(hint, keys):
    """Directions ordered by how well they fit the idea's words (and dark/light wishes)."""
    words = set(re.findall(r"[a-z0-9][a-z0-9-]+", (hint or "").lower()))
    want = "dark" if words & {"dark", "night", "neon", "black"} else "light" if words & {"light", "bright", "white", "airy"} else None
    def score(k):
        s = 2 * len(words & set(AFFINITY.get(k, "").split()))
        if want and DIRECTIONS[k]["mode"] == want:
            s += 1.5
        elif want:
            s -= 1.5
        return s
    return sorted(keys, key=score, reverse=True), score


def options(project, n=3, game=None, brand=None, hint=""):
    """n briefs in clearly different directions (game projects get a game direction among them).
    With a hint (the idea in the user's words), the best-fitting directions come first, plus one wildcard."""
    rng = random.Random()
    keys = list(DIRECTIONS)
    game_keys = [k for k in keys if k.startswith("game-")]
    web_keys = [k for k in keys if not k.startswith("game-")]
    if hint:
        rng.shuffle(game_keys)
        rng.shuffle(web_keys)
        gk, gscore = rank_directions(hint, game_keys)
        wk, wscore = rank_directions(hint, web_keys)
        if game:
            picks = gk[:min(2, n)] + wk[:max(0, n - 2)]
        else:
            fits = [k for k in wk if wscore(k) > 0][:n - 1] or wk[:1]
            rest = [k for k in web_keys if k not in fits]
            picks = (fits + rng.sample(rest, n - len(fits)))[:n]
    elif game:
        picks = rng.sample(game_keys, min(2, n)) + rng.sample(web_keys, max(0, n - 2))
    else:
        picks = rng.sample(web_keys, n)
    return [make_brief(project, k, seed=rng.randrange(1 << 30), brand=brand) for k in picks]


# ------------------------------------------------------------------ websites: Tailwind CSS + daisyUI, themed by the brief
DAISY_CDN = "https://cdn.jsdelivr.net/npm/daisyui@5"
TAILWIND_CDN = "https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"


def _on(bg):
    """Readable text colour on top of a colour (black or white, whichever contrasts more)."""
    return "#111111" if contrast("#111111", bg) >= contrast("#ffffff", bg) else "#ffffff"


def fonts_link(b):
    q = "&".join(f"family={f.replace(' ', '+')}:wght@400;500;600;700" for f in dict.fromkeys(b["fonts"].values()))
    return f"https://fonts.googleapis.com/css2?{q}&display=swap"


def web_theme_css(b):
    """The brief as a daisyUI theme ('brand') + Tailwind font tokens. Lives in css/theme.css."""
    p, r = b["palette"], int(b.get("radius_px", 8))
    on = {k: _on(p[k]) for k in ("accent", "success", "warning", "danger")}
    depth = 0 if str(b.get("shadow", "")).lower().startswith(("none", "flat", "no ")) else 1
    return f"""/* Generated by Windmill from the design brief (.windmill/design.md). Re-pick the look: windmill design */
[data-theme="brand"] {{
  color-scheme: {b.get('mode', 'light')};
  --color-base-100: {p['bg']};
  --color-base-200: {p['surface']};
  --color-base-300: {p['surface-2']};
  --color-base-content: {p['text']};
  --color-primary: {p['primary']};
  --color-primary-content: {p['on-primary']};
  --color-secondary: {p['accent']};
  --color-secondary-content: {on['accent']};
  --color-accent: {p['accent']};
  --color-accent-content: {on['accent']};
  --color-neutral: {p['text']};
  --color-neutral-content: {p['bg']};
  --color-info: {p['primary']};
  --color-info-content: {p['on-primary']};
  --color-success: {p['success']};
  --color-success-content: {on['success']};
  --color-warning: {p['warning']};
  --color-warning-content: {on['warning']};
  --color-error: {p['danger']};
  --color-error-content: {on['danger']};
  --radius-box: {r}px;
  --radius-field: {min(r, 12)}px;
  --radius-selector: {min(r, 16)}px;
  --border: 1px;
  --depth: {depth};
  --noise: 0;
  --color-muted: {p['text-muted']};
  --color-line: {p['border']};
  font-family: "{b['fonts']['body']}", system-ui, sans-serif;
}}
[data-theme="brand"] h1, [data-theme="brand"] h2, [data-theme="brand"] h3, [data-theme="brand"] .font-display {{
  font-family: "{b['fonts']['display']}", "{b['fonts']['body']}", serif;
}}
"""


def write_web_theme(root, brief=None):
    """Write css/theme.css for a website project (called on new projects and when the look changes)."""
    root = Path(root)
    if brief is None:
        f = root / ".windmill" / "design.json"
        if not f.exists():
            return None
        brief = json.loads(f.read_text(encoding="utf-8"))
    out = (root / "static" / "css" / "theme.css") if (root / "Cargo.toml").exists() else (root / "css" / "theme.css")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(web_theme_css(brief), encoding="utf-8")
    return out


def is_web_project(root):
    root = Path(root)
    return (root / "index.html").exists() or (root / "css" / "theme.css").exists() or \
        (root / "static" / "css" / "theme.css").exists() or \
        "tailwind" in ((root / "AGENTS.md").read_text(encoding="utf-8", errors="ignore").lower()
                       if (root / "AGENTS.md").exists() else "")


def brief_markdown(b):
    p = b["palette"]
    css = "\n".join(f"  --{k}: {v};" for k, v in p.items())
    fonts_q = "&".join(f"family={f.replace(' ', '+')}:wght@400;500;600;700" for f in dict.fromkeys(b["fonts"].values()))
    ts = b["type_scale_px"]
    rust = "\n".join(f"pub const {k.upper().replace('-', '_')}: Color = Color::srgb_u8({int(v[1:3], 16)}, {int(v[3:5], 16)}, {int(v[5:7], 16)});"
                     for k, v in p.items())
    return f"""# Design brief - {b['project']}
Direction: **{b['label']}** ({b['mode']} theme). Follow this for EVERY screen so the product has one
distinct identity. Don't fall back to generic defaults.

## Signature moves (what makes this look like *this* product)
{chr(10).join('- ' + m for m in b['signature_moves'])}

## Avoid
{chr(10).join('- ' + m for m in b['avoid'])}

## Colour tokens (contrast-checked: text/bg {b['contrast']['text/bg']}:1, muted {b['contrast']['muted/bg']}:1, button text {b['contrast']['on-primary/primary']}:1)
Use tokens, never raw hex in components. 60% bg/surface, 30% text/neutral, 10% primary+accent.
```css
:root {{
{css}
  --font-display: "{b['fonts']['display']}", serif;
  --font-body: "{b['fonts']['body']}", system-ui, sans-serif;
  --radius: {b['radius_px']}px;
}}
```
Fonts (free, Google Fonts): `<link href="https://fonts.googleapis.com/css2?{fonts_q}&display=swap" rel="stylesheet">`

## Type
Display: {b['fonts']['display']} - Body: {b['fonts']['body']}
Scale (px): small {ts['small']}, body {ts['body']}, h4 {ts['h4']}, h3 {ts['h3']}, h2 {ts['h2']}, h1 {ts['h1']}, display {ts['display']}.
Body line-height 1.5-1.65, headings 1.05-1.2. Line length 60-75 characters.

## Space, shape, depth, motion
Spacing scale (px): {', '.join(map(str, b['spacing_px']))} - use ONLY these.
Corner radius: {b['radius_px']}px. Shadows: {b['shadow']}. Motion: {b['motion']}.
Always respect `prefers-reduced-motion`.

## Websites and web pages: Tailwind CSS + daisyUI (no build step)
Every page's `<head>` (in this order), and `<html lang="en" data-theme="brand">`:
```html
<link href="{fonts_link(b)}" rel="stylesheet">
<link href="{DAISY_CDN}" rel="stylesheet" type="text/css">
<script src="{TAILWIND_CDN}"></script>
<link href="css/theme.css" rel="stylesheet">
<link href="css/site.css" rel="stylesheet">
```
- `css/theme.css` is this brief as the daisyUI theme "brand" - the colours, radius and fonts above. Use theme
  colours by name (`bg-base-100`, `bg-base-200`, `text-base-content`, `btn-primary`, `text-primary`, `badge-accent`,
  `alert-error`...), never raw hex. Display font: class `font-display` (h1-h3 get it automatically).
- Use daisyUI components instead of hand-rolling them: `btn`, `card`, `navbar`, `menu`, `dropdown`, `modal` (with
  `<dialog>`), `drawer`, `tabs`, `collapse`, `fieldset` + `label` + `input`/`select`/`textarea`/`checkbox`/`toggle`,
  `alert`, `toast`, `badge`, `table`, `stat`, `steps`, `skeleton`, `loading`, `avatar`, `footer`, `hero`.
- Layout and spacing with Tailwind utilities (flex/grid, gap, padding, responsive `sm:`/`md:`/`lg:`), using the
  spacing scale above.
- `css/site.css` is only for the signature moves above (things utilities can't express). Keep it small.

## Rust (Bevy) colour constants
```rust
use bevy::prelude::Color;
{rust}
```
"""


def save(root, brief):
    d = Path(root) / ".windmill"
    d.mkdir(exist_ok=True)
    (d / "design.json").write_text(json.dumps(brief, indent=2), encoding="utf-8")
    (d / "design.md").write_text(brief_markdown(brief), encoding="utf-8")
    if is_web_project(root):
        write_web_theme(root, brief)
    return d / "design.md"


def load(root):
    j = Path(root) / ".windmill" / "design.json"
    if j.exists():  # regenerate, so older projects get newer guidance (e.g. the Tailwind + daisyUI section)
        try:
            return brief_markdown(json.loads(j.read_text(encoding="utf-8")))
        except (ValueError, KeyError):
            pass
    f = Path(root) / ".windmill" / "design.md"
    return f.read_text(encoding="utf-8") if f.exists() else None


# ------------------------------------------------------------------ rules + critic
UI_RULES = """# Professional UI rules (apply to every screen)
Hierarchy: one clear primary action per view; size/weight/colour establish order; squint test passes.
Layout: consistent spacing scale only; align to a grid; group related items; whitespace > borders.
Typography: max 2 families (from the brief); real type scale; never centre long text; 60-75ch lines.
Colour: tokens only; text contrast >= 4.5:1 (body aims 7:1); never convey meaning by colour alone.
States - EVERY interactive element has: default, hover, focus-visible (clear ring), active/pressed, disabled.
Every data view has: loading (skeleton, not spinner-only), empty (helpful, with next action), error (says what
happened + how to fix), success feedback. Feedback within 100ms of any click.
Responsive: mobile-first; works at 360px, 768px, 1280px+; touch targets >= 44px; no horizontal scroll.
Motion: 150-300ms ease-out for UI; animate opacity/transform only; honour prefers-reduced-motion.
Copy: real, specific, benefit-led words. No lorem ipsum, no "Welcome to our website", no placeholder images.
Icons: one consistent inline-SVG set, same stroke width; icons pair with labels for important actions.
Forms: visible labels (not placeholder-only), inline validation, helpful errors, keyboard friendly.
Accessibility: semantic HTML (header/nav/main/button), alt text, labels, focus order, skip link.
Distinctiveness: use the brief's signature moves. Don't default to: centred hero + gradient blob + three
identical feature cards + generic testimonials. Pick layouts that fit THIS content.
Performance: optimise images (width/height set, lazy-load below fold), system/web fonts with swap.
Game UI extra: readable at a glance over gameplay; HUD minimal and at screen edges; full keyboard/controller
focus navigation; juice (feedback animation + sound hook) on every action; tooltips for every item/stat;
rarity/state shown by shape/icon as well as colour; inventory slots show count, hover, drag ghost, invalid-drop."""

UI_CRITIC = """You are a design director at a top product studio reviewing UI work. Be demanding.
Score each 1-5 and cite specific files/selectors:
1. Follows the DESIGN BRIEF (tokens, fonts, radius, signature moves) - or did it drift to generic defaults?
2. Visual hierarchy and one clear primary action per view
3. Spacing rhythm and alignment (only scale values, consistent)
4. Typography quality (scale, line-length, line-height, pairing)
5. Interaction states (hover, focus-visible, active, disabled, loading, empty, error)
6. Responsive behaviour (mobile 360px through desktop)
7. Accessibility (contrast, semantics, labels, keyboard)
8. Copy quality (real, specific; no lorem/placeholder)
9. Distinctiveness - would this be mistaken for a generic template?
List the concrete fixes. Final line: VERDICT: PASS only if every score >= 4, else VERDICT: FAIL"""

UI_HINT = re.compile(r"\b(ui|ux|page|screen|layout|style|css|html|design|button|menu|hud|inventory|landing|"
                     r"website|site|dashboard|form|modal|theme|colou?r|font|responsive|mobile|component|view)\b", re.I)
UI_EXT = (".html", ".css", ".scss", ".tsx", ".jsx", ".vue", ".svelte", ".astro")


def is_ui(text="", files=()):
    return bool(UI_HINT.search(text or "")) or any(str(f).endswith(UI_EXT) or "/ui" in str(f).replace("\\", "/")
                                                    for f in files)
