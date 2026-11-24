/* Animation player: draws a reel's frame for any time t.
 *
 * One file for the studio's live preview (an iframe, driven by postMessage) and for the
 * renderer (headless Chromium, driven by page.evaluate), so the preview is the render.
 * Everything on screen is a pure function of t: no CSS animations or transitions, no
 * clock, and randomness only from seeded generators. Text is always set as textContent.
 *
 * Data (animate/timeline.py player_data): {style, title, duration, scenes: [{kind, start,
 * end, voiceStart, voiceEnd, beats, words, visual}], captions: [{start, end, words}],
 * header: {start}}. API: Player.load(data, {fontBase}) -> Promise, Player.seek(t),
 * Player.problems() -> texts that had to shrink below their smallest size.
 */
(function () {
  "use strict";

  const W = 1080;
  const H = 1920;
  // Instagram's own buttons and caption cover these; nothing to read goes there.
  const SAFE = { top: 220, bottom: 420, right: 150 };
  const BOX = { x: 80, y: 340, w: 920, h: 800 }; // where a scene's visual goes
  const SUBS = { y: 1180, h: 250, w: 860 }; // subtitles band
  const ENTER = 0.4;
  const EXIT = 0.26;

  const FONTS = [
    "Montserrat-ExtraBold",
    "Montserrat-Bold",
    "Inter-SemiBold",
    "Inter-Bold",
    "Inter-Medium",
    "Anton-Regular",
    "SourceSerif4-Bold",
    "SourceSerif4-Semibold",
    "Rubik-ExtraBold",
    "Rubik-Bold",
    "Rubik-SemiBold",
    "PatrickHand-Regular",
    "Poppins-SemiBold",
  ];

  const THEMES = {
    glow: {
      bg: "#05060f",
      text: "#f4f7ff",
      muted: "rgba(214,226,255,0.74)",
      accent: "#38e1ff",
      accent2: "#ff4fd8",
      accent3: "#ffd84f",
      card: "rgba(18,24,56,0.66)",
      ink: "#05060f",
      display: "Montserrat-ExtraBold",
      body: "Inter-SemiBold",
      number: "Anton-Regular",
      caption: "Montserrat-ExtraBold",
      quote: "SourceSerif4-Bold",
      glow: true,
      palette: "glow",
    },
    paper: {
      bg: "#f5efe3",
      text: "#1f1c18",
      muted: "rgba(45,40,33,0.72)",
      accent: "#ff5a36",
      accent2: "#2f6bff",
      accent3: "#ffd23f",
      card: "#fffdf8",
      ink: "#1f1c18",
      display: "SourceSerif4-Bold",
      body: "Inter-SemiBold",
      number: "Rubik-ExtraBold",
      caption: "Rubik-ExtraBold",
      quote: "SourceSerif4-Bold",
      glow: false,
      paper: true,
      palette: "paper",
    },
    story: {
      bg: "#FFF4E6",
      text: "#2A2420",
      muted: "rgba(42,36,32,0.72)",
      accent: "#FF6B4A",
      accent2: "#3D7BF2",
      accent3: "#FFC93C",
      card: "#ffffff",
      ink: "#2A2420",
      display: "Rubik-ExtraBold",
      body: "Rubik-SemiBold",
      number: "Rubik-ExtraBold",
      caption: "Rubik-ExtraBold",
      quote: "Rubik-Bold",
      glow: false,
      story: true,
      palette: "story",
    },
    stick: {
      bg: "#FFFFFF",
      text: "#111111",
      muted: "rgba(17,17,17,0.72)",
      accent: "#E53935",
      accent2: "#1E88E5",
      accent3: "#FFD54F",
      card: "#ffffff",
      ink: "#111111",
      display: "PatrickHand-Regular",
      body: "PatrickHand-Regular",
      number: "PatrickHand-Regular",
      caption: "PatrickHand-Regular",
      quote: "PatrickHand-Regular",
      glow: false,
      stick: true,
      palette: "stick",
    },
    kinetic: {
      bg: "#0A0A0A",
      text: "#FFFFFF",
      muted: "rgba(255,255,255,0.7)",
      accent: "#FFE500",
      accent2: "#FF3B30",
      accent3: "#00E0FF",
      card: "#161616",
      ink: "#FFFFFF",
      display: "Anton-Regular",
      body: "Montserrat-Bold",
      number: "Anton-Regular",
      caption: "Montserrat-ExtraBold",
      quote: "Anton-Regular",
      glow: false,
      kinetic: true,
      palette: "glow",
    },
    // World map: a satellite map under everything (see "the World map style" below); other
    // visuals on it are drawn the Neon Glow way over a darkened map.
    map: {
      bg: "#00293b",
      text: "#FFFFFF",
      muted: "rgba(255,255,255,0.8)",
      accent: "#FFD23F",
      accent2: "#FF5A5F",
      accent3: "#FFD23F",
      card: "rgba(8,18,28,0.72)",
      ink: "#08121c",
      display: "Anton-Regular",
      body: "Poppins-SemiBold",
      number: "Anton-Regular",
      caption: "Poppins-SemiBold",
      quote: "Poppins-SemiBold",
      glow: true,
      map: true,
      palette: "glow",
    },
  };

  // ---- maths --------------------------------------------------------------------------

  const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
  const lerp = (a, b, p) => a + (b - a) * p;
  const prog = (t, start, dur) => (dur <= 0 ? (t >= start ? 1 : 0) : clamp((t - start) / dur));
  const ease = {
    out: (p) => 1 - Math.pow(1 - p, 3),
    inOut: (p) => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2),
    back: (p) => {
      const c1 = 1.70158;
      const c3 = c1 + 1;
      return 1 + c3 * Math.pow(p - 1, 3) + c1 * Math.pow(p - 1, 2);
    },
    bounce: (p) => {
      const n1 = 7.5625;
      const d1 = 2.75;
      if (p < 1 / d1) return n1 * p * p;
      if (p < 2 / d1) return n1 * (p -= 1.5 / d1) * p + 0.75;
      if (p < 2.5 / d1) return n1 * (p -= 2.25 / d1) * p + 0.9375;
      return n1 * (p -= 2.625 / d1) * p + 0.984375;
    },
  };

  function rng(seed) {
    let s = (seed >>> 0) || 1;
    return () => {
      s = (s + 0x6d2b79f5) >>> 0;
      let r = Math.imul(s ^ (s >>> 15), 1 | s);
      r = (r + Math.imul(r ^ (r >>> 7), 61 | r)) ^ r;
      return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
    };
  }

  function hash(text) {
    let h = 2166136261;
    for (let i = 0; i < text.length; i++) {
      h ^= text.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return h >>> 0;
  }

  // ---- DOM helpers --------------------------------------------------------------------

  function el(tag, cls, parent, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    if (parent) parent.appendChild(node);
    return node;
  }

  function place(node, x, y, w, h) {
    node.style.left = x + "px";
    node.style.top = y + "px";
    if (w !== undefined) node.style.width = w + "px";
    if (h !== undefined) node.style.height = h + "px";
    return node;
  }

  function font(node, family, size, color) {
    node.style.fontFamily = `"${family}", "NotoColorEmoji", sans-serif`;
    if (size) node.style.fontSize = size + "px";
    if (color) node.style.color = color;
    return node;
  }

  const shrunk = [];

  // The biggest font size (max..min) at which `node`'s text fits its box; measured once the
  // fonts are in. Text that doesn't fit even at `min` is noted (Player.problems).
  function fit(node, max, min, label) {
    const w = node.clientWidth;
    const h = node.clientHeight;
    let lo = min;
    let hi = max;
    const fits = (size) => {
      node.style.fontSize = size + "px";
      return node.scrollWidth <= w + 1 && node.scrollHeight <= h + 1;
    };
    if (fits(max)) return max;
    while (hi - lo > 1) {
      const mid = Math.floor((lo + hi) / 2);
      if (fits(mid)) lo = mid;
      else hi = mid;
    }
    if (!fits(lo)) shrunk.push(label || node.textContent.slice(0, 40));
    node.style.fontSize = lo + "px";
    return lo;
  }

  // Words as spans inside a plain block: a flex box drops the spaces between its children.
  function words(node, text, cls) {
    const spans = [];
    const line = el("div", "line", node);
    String(text)
      .split(/(\s+)/)
      .forEach((part) => {
        if (!part) return;
        if (/^\s+$/.test(part)) {
          line.appendChild(document.createTextNode(" "));
          return;
        }
        spans.push(el("span", "word" + (cls ? " " + cls : ""), line, part));
      });
    return spans;
  }

  function show(node, opacity, transform) {
    node.style.opacity = opacity.toFixed(4);
    if (transform !== undefined) node.style.transform = transform;
  }

  function glowText(node, theme, color, strength = 1) {
    if (theme.map) {
      // On a map a shadow reads better than a glow.
      node.style.textShadow = "0 6px 18px rgba(0,0,0,0.7), 0 2px 0 rgba(0,0,0,0.45)";
      return;
    }
    if (!theme.glow) return;
    const c = color || theme.accent;
    node.style.textShadow = `0 0 ${18 * strength}px ${c}55, 0 0 ${48 * strength}px ${c}33`;
  }

  // ---- normalising times --------------------------------------------------------------

  const plain = (s) =>
    String(s || "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "");

  // When each of `items` is spoken: the first word of the narration from `scene.beats`
  // (worked out by timeline.py), else spread over the narration.
  function beatsOf(scene, n) {
    const beats = (scene.beats || []).slice(0, n);
    const span = Math.max(0.6, (scene.voiceEnd || scene.end) - (scene.voiceStart || scene.start));
    const first = (scene.voiceStart || scene.start) + 0.05;
    for (let i = beats.length; i < n; i++) beats.push(first + (span * 0.8 * i) / Math.max(1, n));
    return beats;
  }

  // ---- backgrounds --------------------------------------------------------------------

  function background(theme, stage, seed) {
    const canvas = el("canvas", "layer", stage);
    canvas.width = W;
    canvas.height = H;
    const ctx = canvas.getContext("2d");
    const r = rng(seed);
    if (theme.stick) {
      return () => {
        ctx.globalAlpha = 1;
        ctx.fillStyle = theme.bg;
        ctx.fillRect(0, 0, W, H);
      };
    }
    if (theme.kinetic) {
      return (t) => {
        ctx.globalAlpha = 1;
        ctx.fillStyle = theme.bg;
        ctx.fillRect(0, 0, W, H);
        const v = ctx.createRadialGradient(540, 860, 200, 540, 860, 1200);
        v.addColorStop(0, "rgba(255,229,0,0.05)");
        v.addColorStop(1, "rgba(0,0,0,0)");
        ctx.fillStyle = v;
        ctx.fillRect(0, 0, W, H);
      };
    }
    if (theme.story) {
      const blobs = [
        { c: theme.accent3, x: 160, y: 380, rad: 300, p: 0.6 },
        { c: theme.accent2, x: 940, y: 1500, rad: 340, p: 2.2 },
        { c: theme.accent, x: 900, y: 420, rad: 160, p: 4.1 },
      ];
      return (t) => {
        ctx.globalAlpha = 1;
        const g = ctx.createLinearGradient(0, 0, 0, H);
        g.addColorStop(0, "#FFF4E6");
        g.addColorStop(1, "#FFE3CC");
        ctx.fillStyle = g;
        ctx.fillRect(0, 0, W, H);
        for (const b of blobs) {
          ctx.globalAlpha = 0.16;
          ctx.fillStyle = b.c;
          ctx.beginPath();
          ctx.arc(b.x + 40 * Math.sin(t * 0.2 + b.p), b.y + 30 * Math.cos(t * 0.17 + b.p), b.rad, 0, Math.PI * 2);
          ctx.fill();
        }
        ctx.globalAlpha = 1;
      };
    }
    if (theme.glow) {
      const stars = Array.from({ length: 150 }, () => ({
        x: r() * W,
        y: r() * H,
        s: 0.8 + r() * 2.2,
        p: r() * Math.PI * 2,
        v: 4 + r() * 10,
      }));
      const blobs = [
        { c: theme.accent, x: 260, y: 520, rad: 640, p: 0.3 },
        { c: theme.accent2, x: 860, y: 1300, rad: 700, p: 2.1 },
        { c: "#7b5cff", x: 520, y: 980, rad: 560, p: 4.2 },
      ];
      return (t) => {
        ctx.globalCompositeOperation = "source-over";
        ctx.globalAlpha = 1;
        const base = ctx.createRadialGradient(540, 760, 80, 540, 900, 1400);
        base.addColorStop(0, "#111748");
        base.addColorStop(1, theme.bg);
        ctx.fillStyle = base;
        ctx.fillRect(0, 0, W, H);
        ctx.globalCompositeOperation = "lighter";
        for (const b of blobs) {
          const x = b.x + 170 * Math.sin(t * 0.11 + b.p);
          const y = b.y + 130 * Math.cos(t * 0.09 + b.p * 1.3);
          const g = ctx.createRadialGradient(x, y, 0, x, y, b.rad);
          g.addColorStop(0, b.c + "2e");
          g.addColorStop(1, b.c + "00");
          ctx.fillStyle = g;
          ctx.fillRect(0, 0, W, H);
        }
        for (const s of stars) {
          const a = 0.25 + 0.75 * (0.5 + 0.5 * Math.sin(t * 1.6 + s.p));
          ctx.globalAlpha = a * 0.8;
          ctx.fillStyle = "#cfe9ff";
          const y = (((s.y - t * s.v) % H) + H) % H;
          ctx.beginPath();
          ctx.arc(s.x, y, s.s, 0, Math.PI * 2);
          ctx.fill();
        }
        ctx.globalAlpha = 1;
        ctx.globalCompositeOperation = "source-over";
        const v = ctx.createRadialGradient(540, 900, 600, 540, 900, 1250);
        v.addColorStop(0, "rgba(0,0,0,0)");
        v.addColorStop(1, "rgba(0,0,0,0.55)");
        ctx.fillStyle = v;
        ctx.fillRect(0, 0, W, H);
      };
    }
    // Paper: grain, a dotted page and slow soft shapes.
    const grain = document.createElement("canvas");
    grain.width = 540;
    grain.height = 960;
    const gctx = grain.getContext("2d");
    const img = gctx.createImageData(grain.width, grain.height);
    for (let i = 0; i < img.data.length; i += 4) {
      const v = 120 + r() * 135;
      img.data[i] = v;
      img.data[i + 1] = v * 0.97;
      img.data[i + 2] = v * 0.9;
      img.data[i + 3] = 26;
    }
    gctx.putImageData(img, 0, 0);
    const shapes = [
      { c: theme.accent3, x: 180, y: 420, rad: 330, a: 0.22, p: 0.4 },
      { c: theme.accent2, x: 930, y: 1420, rad: 380, a: 0.07, p: 1.9 },
      { c: theme.accent, x: 900, y: 360, rad: 170, a: 0.08, p: 3.3 },
    ];
    return (t) => {
      ctx.globalAlpha = 1;
      ctx.fillStyle = theme.bg;
      ctx.fillRect(0, 0, W, H);
      for (const s of shapes) {
        ctx.globalAlpha = s.a;
        ctx.fillStyle = s.c;
        ctx.beginPath();
        ctx.arc(
          s.x + 40 * Math.sin(t * 0.15 + s.p),
          s.y + 30 * Math.cos(t * 0.12 + s.p),
          s.rad,
          0,
          Math.PI * 2,
        );
        ctx.fill();
      }
      ctx.globalAlpha = 0.11;
      ctx.fillStyle = theme.ink;
      for (let y = 60; y < H; y += 54) {
        for (let x = 54; x < W; x += 54) {
          ctx.beginPath();
          ctx.arc(x, y, 2.1, 0, Math.PI * 2);
          ctx.fill();
        }
      }
      ctx.globalAlpha = 1;
      ctx.drawImage(grain, 0, 0, W, H);
      const v = ctx.createRadialGradient(540, 900, 700, 540, 900, 1300);
      v.addColorStop(0, "rgba(120,90,40,0)");
      v.addColorStop(1, "rgba(120,90,40,0.16)");
      ctx.fillStyle = v;
      ctx.fillRect(0, 0, W, H);
    };
  }

  // ---- pieces several scenes use ------------------------------------------------------

  function card(theme, parent, x, y, w, h, tint) {
    const node = place(el("div", "abs", parent), x, y, w, h);
    node.style.borderRadius = theme.glow ? "36px" : "28px";
    if (theme.glow) {
      node.style.background = theme.card;
      node.style.border = `3px solid ${tint || theme.accent}aa`;
      node.style.boxShadow = `0 0 40px ${tint || theme.accent}40, inset 0 0 30px ${tint || theme.accent}1a`;
    } else {
      node.style.background = theme.card;
      node.style.border = `5px solid ${theme.ink}`;
      node.style.boxShadow = `12px 12px 0 ${theme.ink}`;
    }
    return node;
  }

  function textBox(theme, parent, family, x, y, w, h, text, max, min, color, align) {
    const node = place(el("div", "abs fit center", parent), x, y, w, h);
    font(node, family, max, color || theme.text);
    if (align === "left") {
      node.style.justifyContent = "flex-start";
      node.style.textAlign = "left";
    }
    node.textContent = text;
    node._fit = [max, min];
    return node;
  }

  function emoji(parent, char, size) {
    const node = el("span", "emoji abs", parent, char || "✨");
    node.style.fontSize = size + "px";
    return node;
  }

  // An item's look before and after it arrives: a fade, a rise and a little overshoot.
  function arrive(node, t, at, dx = 0, dy = 40, dur = 0.42) {
    const p = prog(t, at, dur);
    const e = ease.back(p);
    show(node, ease.out(p), `translate(${(1 - e) * dx}px, ${(1 - e) * dy}px) scale(${lerp(0.88, 1, e)})`);
  }

  // ---- scenes -------------------------------------------------------------------------
  // Each builds its DOM inside `root` and returns update(t) for absolute time t.

  const SCENES = {};

  SCENES.title = (v, theme, root, scene) => {
    const parts = [];
    if (v.kicker) {
      const k = textBox(theme, root, theme.body, 140, BOX.y + 20, 800, 70, v.kicker.toUpperCase(), 40, 26, theme.glow ? theme.ink : theme.card);
      k.style.background = theme.glow ? theme.accent : theme.ink;
      k.style.borderRadius = "40px";
      k.style.letterSpacing = "0.12em";
      k.style.width = "auto";
      k.style.left = "50%";
      k.style.padding = "0 34px";
      k.style.transformOrigin = "50% 50%";
      parts.push(k);
      k._center = true;
    }
    const head = place(el("div", "abs fit center", root), BOX.x, BOX.y + 120, BOX.w, BOX.h - 200);
    font(head, theme.display, 128, theme.text);
    head.style.flexWrap = "wrap";
    head.style.alignContent = "center";
    head.style.lineHeight = "1.06";
    head._fit = [132, 64];
    const hl = plain(v.highlight);
    const spans = words(head, theme.map ? String(v.text || "").toUpperCase() : v.text || "");
    spans.forEach((s) => {
      if (hl && hl.includes(plain(s.textContent)) && plain(s.textContent)) {
        s.classList.add("hl");
        if (theme.glow) {
          s.style.color = theme.accent;
          glowText(s, theme, theme.accent, 1.3);
        } else {
          s.style.backgroundImage = `linear-gradient(${theme.accent3}, ${theme.accent3})`;
          s.style.backgroundRepeat = "no-repeat";
          s.style.backgroundPosition = "0 88%";
          s.style.padding = "0 6px";
        }
      } else if (theme.glow) {
        glowText(s, theme, "#9fdcff", 0.6);
      }
    });
    const beats = beatsOf(scene, spans.length);
    const drips = [];
    if (theme.map && building && building.look === "horror") {
      spans.forEach((s, i) => {
        if (!s.classList.contains("hl")) return;
        s.style.color = "#D8141C";
        s.style.position = "relative";
        s.style.zIndex = "3"; // its drips run over the line below
        s.style.display = "inline-block";
        s.style.textShadow = "0 0 18px rgba(216,20,28,0.55), 0 5px 0 #4a0005";
        const r = rng(hash(s.textContent) + i);
        for (let k = 0; k < 6; k++) {
          const drip = el("span", "", s);
          drip.style.position = "absolute";
          drip.style.left = `${(8 + k * 15 + r() * 8).toFixed(1)}%`;
          drip.style.top = "80%";
          drip.style.width = `${(10 + r() * 8).toFixed(1)}px`;
          drip.style.borderRadius = "0 0 10px 10px";
          drip.style.zIndex = "3";
          drip.style.background = "linear-gradient(#D8141C, #8a0008)";
          drip.style.boxShadow = "0 0 10px rgba(216,20,28,0.5)";
          drips.push({ node: drip, at: beats[i] + 0.2 + r() * 0.4, most: 50 + r() * 80 });
        }
      });
    }
    return (t) => {
      drips.forEach((d) => (d.node.style.height = `${(d.most * ease.out(prog(t, d.at, 1.8))).toFixed(1)}px`));
      parts.forEach((k) => {
        const p = ease.back(prog(t, scene.start, 0.45));
        show(k, clamp(p), `translateX(-50%) scale(${lerp(0.6, 1, p)})`);
      });
      spans.forEach((s, i) => {
        const at = beats[i];
        const p = prog(t, at, 0.38);
        const e = ease.back(p);
        show(s, ease.out(p), `translateY(${(1 - e) * 46}px) scale(${lerp(0.82, 1, e)})`);
        if (s.classList.contains("hl") && !theme.glow) {
          const m = ease.out(prog(t, at + 0.25, 0.4));
          s.style.backgroundSize = `${(m * 100).toFixed(1)}% 42%`;
        }
      });
    };
  };

  function parseValue(value) {
    const m = String(value || "").match(/^([^\d-]*)(-?[\d,]*\.?\d+)(.*)$/);
    if (!m) return null;
    const digits = m[2];
    const num = parseFloat(digits.replace(/,/g, ""));
    if (!isFinite(num)) return null;
    const decimals = (digits.split(".")[1] || "").length;
    return { head: m[1], num, decimals, commas: digits.includes(","), tail: m[3] };
  }

  function formatValue(parsed, x) {
    let s = x.toFixed(parsed.decimals);
    if (parsed.commas) {
      const [int, dec] = s.split(".");
      s = int.replace(/\B(?=(\d{3})+(?!\d))/g, ",") + (dec ? "." + dec : "");
    }
    return s;
  }

  SCENES.stat = (v, theme, root, scene) => {
    const parsed = parseValue(v.value);
    const head = (v.prefix || "") + (parsed ? parsed.head : "");
    const tail = ((parsed ? parsed.tail : "") + " " + (v.suffix || "")).trim();
    const cy = BOX.y + 330;
    const R = 300;
    let ring = null;
    if (theme.glow) {
      ring = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      ring.setAttribute("width", String(2 * R + 40));
      ring.setAttribute("height", String(2 * R + 40));
      ring.style.position = "absolute";
      ring.style.left = 540 - R - 20 + "px";
      ring.style.top = cy - R - 20 + "px";
      ring.style.filter = `drop-shadow(0 0 18px ${theme.accent})`;
      const c0 = R + 20;
      const track = document.createElementNS(ring.namespaceURI, "circle");
      const arc = document.createElementNS(ring.namespaceURI, "circle");
      for (const c of [track, arc]) {
        c.setAttribute("cx", String(c0));
        c.setAttribute("cy", String(c0));
        c.setAttribute("r", String(R));
        c.setAttribute("fill", "none");
        c.setAttribute("stroke-width", c === arc ? "14" : "6");
        c.setAttribute("stroke-linecap", "round");
        ring.appendChild(c);
      }
      track.setAttribute("stroke", "rgba(120,170,255,0.18)");
      arc.setAttribute("stroke", theme.accent);
      arc.setAttribute("transform", `rotate(-90 ${c0} ${c0})`);
      arc.setAttribute("stroke-dasharray", String(2 * Math.PI * R));
      ring._arc = arc;
      root.appendChild(ring);
    } else {
      const disc = place(el("div", "abs", root), 540 - R, cy - R, 2 * R, 2 * R);
      disc.style.borderRadius = "50%";
      disc.style.background = theme.accent3;
      disc.style.border = `6px solid ${theme.ink}`;
      disc.style.boxShadow = `14px 14px 0 ${theme.ink}`;
      ring = disc;
    }
    const num = place(el("div", "abs fit center", root), 540 - R + 50, cy - 150, 2 * R - 100, tail ? 230 : 300);
    font(num, theme.number, 230, theme.text);
    const line = el("div", "line", num);
    line.style.whiteSpace = "nowrap";
    const hd = el("span", "", line, head);
    hd.style.fontSize = "0.55em";
    hd.style.color = theme.glow ? theme.accent : theme.accent;
    const digits = el("span", "", line, parsed ? formatValue(parsed, parsed.num) : v.value);
    if (theme.glow) glowText(digits, theme, theme.accent, 1.2);
    num._fit = [230, 70];
    const unit = textBox(theme, root, theme.display, 540 - R + 60, cy + 85, 2 * R - 120, 90, tail, 66, 30, theme.glow ? theme.accent : theme.ink);
    if (theme.glow) glowText(unit, theme, theme.accent, 0.8);
    const label = textBox(theme, root, theme.body, BOX.x + 20, cy + R + 40, BOX.w - 40, 120, v.text || "", 58, 32, theme.glow ? theme.muted : theme.text);
    const [at, labelAt] = beatsOf(scene, 2);
    const count = 1.3;
    return (t) => {
      const p = ease.out(prog(t, at, count));
      if (parsed) digits.textContent = formatValue(parsed, parsed.num * p);
      // Up as the scene comes in (never an empty screen); the count runs as it is said.
      const up = Math.min(at - 0.15, scene.start + 0.1);
      const pop = ease.back(prog(t, up, 0.45));
      const kick = 1 + 0.06 * Math.sin(Math.PI * prog(t, at + count - 0.1, 0.3));
      show(num, clamp(pop * 1.4), `scale(${lerp(0.7, 1, pop) * kick})`);
      show(unit, ease.out(prog(t, up + 0.25, 0.4)));
      if (theme.glow) {
        ring._arc.setAttribute("stroke-dashoffset", String(2 * Math.PI * R * (1 - p)));
        show(ring, ease.out(prog(t, up - 0.05, 0.4)), `rotate(${(t - scene.start) * 6}deg)`);
      } else {
        const d = ease.back(prog(t, up - 0.1, 0.5));
        show(ring, clamp(d), `scale(${lerp(0.3, 1, d)}) rotate(${lerp(-12, -3, d)}deg)`);
      }
      arrive(label, t, Math.min(labelAt, up + 0.35));
    };
  };

  const MOTIONS = ["pop", "float", "walk_left", "walk_right", "shake", "spin", "grow", "fall", "pulse"];

  SCENES.icons = (v, theme, root, scene) => {
    const items = (v.items || []).slice(0, 3);
    if (!items.length) items.push({ emoji: v.emoji || "✨", motion: "pop" });
    const size = [300, 250, 210][items.length - 1];
    const gap = BOX.w / items.length;
    const floorY = BOX.y + 560;
    const nodes = items.map((item, i) => {
      const cx = BOX.x + gap * (i + 0.5);
      const shadow = place(el("div", "abs", root), cx - size * 0.45, floorY + 10, size * 0.9, 46);
      shadow.style.borderRadius = "50%";
      shadow.style.background = theme.glow
        ? `radial-gradient(closest-side, ${theme.accent}66, ${theme.accent}00)`
        : "radial-gradient(closest-side, rgba(0,0,0,0.22), rgba(0,0,0,0))";
      const e = emoji(root, item.emoji, size);
      place(e, cx - size / 2, floorY - size, size, size);
      e.style.textAlign = "center";
      if (theme.glow) e.style.filter = `drop-shadow(0 0 26px ${theme.accent}88)`;
      return { e, shadow, motion: MOTIONS.includes(item.motion) ? item.motion : "pop" };
    });
    const label = textBox(theme, root, theme.display, BOX.x, BOX.y + 640, BOX.w, 170, v.text || "", 72, 40, theme.text);
    if (theme.glow) glowText(label, theme, "#9fdcff", 0.6);
    const beats = beatsOf(scene, nodes.length + 1);
    return (t) => {
      nodes.forEach((n, i) => {
        const at = beats[i];
        const lt = t - at;
        const p = prog(t, at, 0.5);
        let x = 0;
        let y = 0;
        let s = 1;
        let r = 0;
        let o = ease.out(p);
        switch (n.motion) {
          case "float":
            y = (1 - ease.out(p)) * 80 + 16 * Math.sin(lt * 2.2 + i);
            break;
          case "walk_left":
          case "walk_right": {
            const dir = n.motion === "walk_left" ? 1 : -1;
            const w = ease.out(prog(t, at, 1.3));
            x = dir * (1 - w) * 640;
            y = -Math.abs(Math.sin(lt * 9)) * 26 * (1 - w * 0.75);
            r = Math.sin(lt * 9) * 5 * (1 - w * 0.6);
            o = clamp(p * 2);
            break;
          }
          case "shake":
            s = ease.back(p);
            r = lt > 0.4 ? Math.sin(lt * 34) * 9 * Math.max(0, 1 - ((lt - 0.4) % 2.2) / 0.7) : 0;
            break;
          case "spin":
            s = ease.back(p);
            r = (1 - ease.out(prog(t, at, 0.9))) * -360;
            break;
          case "grow":
            s = lerp(0.55, 1.12, ease.inOut(prog(t, at, Math.max(1, scene.end - at))));
            break;
          case "fall":
            y = -(1 - ease.bounce(prog(t, at, 0.9))) * 760;
            o = clamp(p * 3);
            break;
          case "pulse": {
            s = ease.back(p);
            const beat = lt > 0.5 ? (lt * 1.25) % 1 : 1;
            s *= 1 + 0.12 * Math.max(0, Math.sin(beat * Math.PI * 2)) * (beat < 0.5 ? 1 : 0);
            break;
          }
          default:
            s = ease.back(p) * (1 + 0.025 * Math.sin(lt * 2.4 + i));
        }
        show(n.e, o, `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px) rotate(${r.toFixed(2)}deg) scale(${s.toFixed(4)})`);
        const lift = clamp(1 + y / 300, 0.4, 1);
        show(n.shadow, o * lift, `translateX(${x.toFixed(1)}px) scale(${(lift * s).toFixed(3)}, 1)`);
      });
      arrive(label, t, beats[nodes.length] || beats[0] + 0.4);
    };
  };

  SCENES.compare = (v, theme, root, scene) => {
    const items = (v.items || []).slice(0, 2);
    while (items.length < 2) items.push({ emoji: "❓", text: "", value: "" });
    const tints = [theme.accent, theme.accent2];
    const cards = items.map((item, i) => {
      const x = i === 0 ? 70 : 560;
      const c = card(theme, root, x, BOX.y + 70, 450, 600, tints[i]);
      const e = emoji(c, item.emoji, 170);
      place(e, 0, 60, 450 - 10, 190);
      e.style.textAlign = "center";
      e.style.width = "100%";
      const label = textBox(theme, c, theme.body, 30, 280, 390, 120, item.text || "", 50, 28, theme.muted);
      const value = textBox(theme, c, theme.number, 20, 410, 410, 150, item.value || "", 96, 40, theme.glow ? tints[i] : theme.text);
      if (theme.glow) glowText(value, theme, tints[i], 1);
      return { c, parts: [label, value] };
    });
    const vs = place(el("div", "abs center", root), 470, BOX.y + 300, 140, 140);
    font(vs, theme.display, 52, theme.glow ? theme.ink : theme.card, "VS");
    vs.textContent = "VS";
    vs.style.borderRadius = "50%";
    vs.style.background = theme.glow ? theme.accent3 : theme.accent;
    vs.style.boxShadow = theme.glow ? `0 0 40px ${theme.accent3}aa` : `6px 6px 0 ${theme.ink}`;
    const beats = beatsOf(scene, 2);
    return (t) => {
      cards.forEach((c, i) => arrive(c.c, t, beats[i], i === 0 ? -260 : 260, 0, 0.55));
      const p = ease.back(prog(t, (beats[0] + beats[1]) / 2, 0.5));
      show(vs, clamp(p), `scale(${lerp(0, 1, p)}) rotate(${(1 - p) * -90}deg)`);
    };
  };

  SCENES.list = (v, theme, root, scene) => {
    const items = (v.items || []).slice(0, 5);
    const title = textBox(theme, root, theme.display, BOX.x, BOX.y, BOX.w, 150, v.text || "", 76, 44, theme.text);
    if (theme.glow) glowText(title, theme, "#9fdcff", 0.6);
    const top = BOX.y + 190;
    const rowH = Math.min(170, (BOX.h - 190) / Math.max(1, items.length));
    const rows = items.map((item, i) => {
      const row = place(el("div", "abs", root), BOX.x, top + i * rowH, BOX.w, rowH - 22);
      row.style.borderRadius = "26px";
      if (theme.glow) {
        row.style.background = theme.card;
        row.style.borderLeft = `8px solid ${i % 2 ? theme.accent2 : theme.accent}`;
      } else {
        row.style.background = theme.card;
        row.style.border = `4px solid ${theme.ink}`;
        row.style.boxShadow = `7px 7px 0 ${theme.ink}`;
      }
      const badge = place(el("div", "abs center", row), 22, (rowH - 22) / 2 - 38, 76, 76);
      badge.style.borderRadius = "50%";
      if (item.emoji) {
        badge.appendChild(emoji(null, item.emoji, 54)).style.position = "static";
      } else {
        font(badge, theme.number, 44, theme.glow ? theme.ink : theme.card);
        badge.textContent = String(i + 1);
        badge.style.background = i % 2 ? theme.accent2 : theme.accent;
      }
      const text = textBox(theme, row, theme.body, 120, 6, BOX.w - 150, rowH - 34, item.text || "", 60, 30, theme.text, "left");
      return { row, text };
    });
    const beats = beatsOf(scene, items.length + 1);
    return (t) => {
      arrive(title, t, beats[0]);
      rows.forEach((r, i) => arrive(r.row, t, beats[i + 1], -120, 0, 0.45));
    };
  };

  SCENES.timeline = (v, theme, root, scene) => {
    const items = (v.items || []).slice(0, 5);
    const lineX = 210;
    const top = BOX.y + 40;
    const span = BOX.h - 80;
    const step = span / Math.max(1, items.length);
    const line = place(el("div", "abs", root), lineX - 4, top, 8, span);
    line.style.background = theme.glow ? `linear-gradient(${theme.accent}, ${theme.accent2})` : theme.ink;
    line.style.borderRadius = "4px";
    line.style.transformOrigin = "50% 0";
    if (theme.glow) line.style.boxShadow = `0 0 24px ${theme.accent}`;
    const rows = items.map((item, i) => {
      const y = top + step * i + step / 2;
      const dot = place(el("div", "abs", root), lineX - 22, y - 22, 44, 44);
      dot.style.borderRadius = "50%";
      dot.style.background = i % 2 ? theme.accent2 : theme.accent;
      dot.style.border = theme.glow ? "6px solid #ffffffcc" : `6px solid ${theme.ink}`;
      if (theme.glow) dot.style.boxShadow = `0 0 30px ${theme.accent}`;
      const year = textBox(theme, root, theme.number, lineX + 60, y - step / 2 + 6, 640, step * 0.46, item.year || "", 76, 36, i % 2 ? theme.accent2 : theme.accent, "left");
      if (theme.glow) glowText(year, theme, theme.accent, 1);
      const text = textBox(theme, root, theme.body, lineX + 60, y - 6, 700, step * 0.5, item.text || "", 46, 26, theme.text, "left");
      return { dot, year, text, y };
    });
    const beats = beatsOf(scene, items.length);
    return (t) => {
      let reach = 0;
      rows.forEach((r, i) => {
        if (t >= beats[i]) reach = Math.max(reach, (r.y - top) / span);
        const p = ease.back(prog(t, beats[i], 0.4));
        show(r.dot, clamp(p), `scale(${p})`);
        arrive(r.year, t, beats[i] + 0.05, 60, 0);
        arrive(r.text, t, beats[i] + 0.15, 60, 0);
      });
      const grow = ease.out(prog(t, scene.start, Math.max(0.6, beats[beats.length - 1] - scene.start + 0.4)));
      show(line, 1, `scaleY(${Math.max(grow * (reach + 0.12), 0.02).toFixed(4)})`);
    };
  };

  SCENES.place = (v, theme, root, scene) => {
    const R = 240;
    const cx = 540;
    const cy = BOX.y + 290;
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("width", String(W));
    svg.setAttribute("height", "700");
    svg.style.position = "absolute";
    svg.style.left = "0";
    svg.style.top = cy - 350 + "px";
    const stroke = theme.glow ? theme.accent : theme.ink;
    if (theme.glow) svg.style.filter = `drop-shadow(0 0 12px ${theme.accent})`;
    const mk = (tag, attrs) => {
      const n = document.createElementNS(svg.namespaceURI, tag);
      for (const k in attrs) n.setAttribute(k, attrs[k]);
      svg.appendChild(n);
      return n;
    };
    mk("circle", { cx, cy: 350, r: R, fill: theme.glow ? "rgba(56,225,255,0.06)" : "#dfe8ee", stroke, "stroke-width": 6 });
    for (const k of [-0.6, -0.3, 0, 0.3, 0.6]) {
      const y = 350 + k * R;
      const half = R * Math.sqrt(1 - k * k);
      mk("ellipse", { cx, cy: y, rx: half, ry: half * 0.12, fill: "none", stroke, "stroke-width": 2.5, opacity: 0.55 });
    }
    const meridians = [0, 1, 2, 3, 4, 5].map(() =>
      mk("ellipse", { cx, cy: 350, rx: R, ry: R, fill: "none", stroke, "stroke-width": 3, opacity: 0.7 }),
    );
    root.appendChild(svg);
    const ripples = [0, 1, 2].map(() => {
      const ring = place(el("div", "abs", root), cx - 60, cy - 30, 120, 60);
      ring.style.borderRadius = "50%";
      ring.style.border = `5px solid ${theme.glow ? theme.accent3 : theme.accent}`;
      return ring;
    });
    const pin = emoji(root, v.emoji || "📍", 170);
    place(pin, cx - 85, cy - 175, 170, 170);
    pin.style.textAlign = "center";
    const name = textBox(theme, root, theme.display, BOX.x, BOX.y + 590, BOX.w, 130, v.text || "", 92, 44, theme.text);
    if (theme.glow) glowText(name, theme, theme.accent, 0.9);
    const detail = textBox(theme, root, theme.body, BOX.x, BOX.y + 720, BOX.w, 80, v.detail || "", 46, 28, theme.muted);
    const [at, nameAt] = beatsOf(scene, 2);
    return (t) => {
      const turn = (t - scene.start) * 0.55;
      meridians.forEach((m, i) => m.setAttribute("rx", String(Math.abs(Math.cos(turn + (i * Math.PI) / 6)) * R)));
      const drop = prog(t, at, 0.8);
      show(pin, clamp(drop * 4), `translateY(${(-(1 - ease.bounce(drop)) * 520).toFixed(1)}px)`);
      ripples.forEach((r, i) => {
        const p = ((t - at - 0.7 - i * 0.45) % 1.6) / 1.6;
        const on = t > at + 0.7 + i * 0.45 ? 1 : 0;
        show(r, on * (1 - p) * 0.9, `scale(${1 + p * 2.6})`);
      });
      arrive(name, t, nameAt);
      arrive(detail, t, nameAt + 0.25);
    };
  };

  SCENES.quote = (v, theme, root, scene) => {
    const mark = place(el("div", "abs", root), BOX.x, BOX.y - 40, 300, 300, "“");
    font(mark, theme.quote, 380, theme.glow ? theme.accent : theme.accent);
    mark.textContent = "“";
    mark.style.lineHeight = "1";
    if (theme.glow) glowText(mark, theme, theme.accent, 1.4);
    const body = place(el("div", "abs fit center", root), BOX.x + 10, BOX.y + 170, BOX.w - 20, 480);
    font(body, theme.quote, 84, theme.text);
    body.style.flexWrap = "wrap";
    body.style.alignContent = "center";
    body.style.lineHeight = "1.18";
    body._fit = [84, 40];
    const spans = words(body, v.text || "");
    const by = textBox(theme, root, theme.body, BOX.x, BOX.y + 680, BOX.w, 90, v.by ? "— " + v.by : "", 48, 28, theme.muted);
    const start = (scene.voiceStart || scene.start) + 0.05;
    const span = Math.max(0.8, ((scene.voiceEnd || scene.end) - start) * 0.85);
    return (t) => {
      arrive(mark, t, scene.start, 0, 30);
      spans.forEach((s, i) => {
        const at = start + (span * i) / Math.max(1, spans.length);
        const p = prog(t, at, 0.35);
        show(s, lerp(0.12, 1, ease.out(p)), `translateY(${(1 - ease.out(p)) * 14}px)`);
      });
      arrive(by, t, start + span);
    };
  };

  SCENES.reveal = (v, theme, root, scene) => {
    const q = place(el("div", "abs center", root), 390, BOX.y + 10, 300, 280, "?");
    font(q, theme.display, 260, theme.glow ? theme.accent2 : theme.accent);
    q.textContent = "?";
    if (theme.glow) glowText(q, theme, theme.accent2, 1.5);
    const question = textBox(theme, root, theme.display, BOX.x, BOX.y + 300, BOX.w, 300, v.text || "", 80, 40, theme.text);
    const flash = place(el("div", "abs", root), -200, BOX.y - 100, W + 400, 1000);
    flash.style.background = theme.glow
      ? `radial-gradient(closest-side, #ffffffcc, ${theme.accent}55, transparent)`
      : `radial-gradient(closest-side, ${theme.accent3}, transparent)`;
    const icon = emoji(root, v.emoji || "💡", 190);
    place(icon, 445, BOX.y + 230, 190, 190);
    icon.style.textAlign = "center";
    const answer = textBox(theme, root, theme.display, BOX.x, BOX.y + 440, BOX.w, 300, v.answer || "", 112, 48, theme.glow ? theme.accent : theme.text);
    if (theme.glow) glowText(answer, theme, theme.accent, 1.4);
    else {
      answer.style.background = theme.accent3;
      answer.style.borderRadius = "24px";
      answer.style.border = `5px solid ${theme.ink}`;
    }
    const beats = beatsOf(scene, 2);
    const at = beats[1];
    return (t) => {
      const before = 1 - ease.out(prog(t, at - 0.1, 0.4));
      const pq = ease.back(prog(t, scene.start, 0.5));
      show(q, clamp(pq) * before, `scale(${lerp(0.4, 1, pq) * (1 + 0.06 * Math.sin((t - scene.start) * 5))})`);
      const up = ease.inOut(prog(t, at - 0.1, 0.45));
      show(question, ease.out(prog(t, scene.start + 0.15, 0.4)) * lerp(1, 0.55, up), `translateY(${lerp(0, -300, up)}px) scale(${lerp(1, 0.62, up)})`);
      const f = prog(t, at, 0.6);
      show(flash, f > 0 && f < 1 ? Math.sin(f * Math.PI) * 0.85 : 0, `scale(${lerp(0.3, 1.2, f)})`);
      const pa = ease.back(prog(t, at + 0.05, 0.5));
      show(icon, clamp(pa), `scale(${pa}) rotate(${(1 - pa) * -25}deg)`);
      show(answer, clamp(pa), `translateY(${(1 - pa) * 60}px) scale(${lerp(0.7, 1, pa)})`);
    };
  };

  SCENES.dots = (v, theme, root, scene) => {
    const cw = W;
    const ch = 700;
    const canvas = place(el("canvas", "abs", root), 0, BOX.y);
    canvas.width = cw;
    canvas.height = ch;
    const ctx = canvas.getContext("2d");
    const label = textBox(theme, root, theme.body, BOX.x, BOX.y + 680, BOX.w, 120, v.text || "", 58, 32, theme.muted);
    let points = null;
    const text = String(v.value || v.text || "").slice(0, 16);
    function sample() {
      const off = document.createElement("canvas");
      off.width = cw;
      off.height = ch;
      const o = off.getContext("2d");
      let size = 380;
      const lines = text.split("\n").slice(0, 2);
      const face = theme.glow ? theme.display : theme.number;
      o.font = `${size}px "${face}"`;
      const widest = () => Math.max(...lines.map((l) => o.measureText(l).width));
      while (size > 60 && (widest() > cw - 160 || size * lines.length * 1.05 > ch - 80)) {
        size -= 8;
        o.font = `${size}px "${face}"`;
      }
      o.fillStyle = "#fff";
      o.textAlign = "center";
      o.textBaseline = "middle";
      lines.forEach((l, i) => o.fillText(l, cw / 2, ch / 2 + (i - (lines.length - 1) / 2) * size * 1.05));
      const data = o.getImageData(0, 0, cw, ch).data;
      const step = Math.max(7, Math.round(size / 26));
      const pts = [];
      for (let y = 0; y < ch; y += step) {
        for (let x = 0; x < cw; x += step) {
          if (data[(y * cw + x) * 4 + 3] > 128) pts.push([x, y]);
        }
      }
      const r = rng(hash(text) || 7);
      // Keep at most 1600 dots, evenly thinned.
      const keep = pts.length > 1600 ? pts.filter((_, i) => i % Math.ceil(pts.length / 1600) === 0) : pts;
      return keep.map(([x, y]) => ({
        x,
        y,
        sx: r() * cw,
        sy: r() * ch * 1.4 - ch * 0.2,
        d: r() * 0.55,
        s: theme.glow ? 3 + r() * 1.8 : 3.4 + r() * 1.6,
        p: r() * Math.PI * 2,
      }));
    }
    const [at, labelAt] = beatsOf(scene, 2);
    return (t) => {
      if (!points) points = sample();
      ctx.clearRect(0, 0, cw, ch);
      ctx.globalCompositeOperation = theme.glow ? "lighter" : "source-over";
      for (const d of points) {
        const p = ease.out(prog(t, at + d.d, 1.1));
        const wob = p >= 1 ? 1.6 : 0;
        const x = lerp(d.sx, d.x, p) + wob * Math.sin(t * 2.3 + d.p);
        const y = lerp(d.sy, d.y, p) + wob * Math.cos(t * 2.1 + d.p);
        const a = clamp(prog(t, Math.min(at - 0.3, scene.start + 0.05) + d.d * 0.5, 0.4));
        if (theme.glow) {
          ctx.globalAlpha = a * 0.16;
          ctx.fillStyle = theme.accent;
          ctx.beginPath();
          ctx.arc(x, y, d.s * 2.6, 0, Math.PI * 2);
          ctx.fill();
          ctx.globalAlpha = a;
          ctx.fillStyle = p >= 1 && Math.sin(t * 3 + d.p) > 0.92 ? "#ffffff" : theme.accent;
        } else {
          ctx.globalAlpha = a;
          ctx.fillStyle = theme.ink;
        }
        ctx.beginPath();
        ctx.arc(x, y, d.s, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = "source-over";
      arrive(label, t, labelAt);
    };
  };

  SCENES.cta = (v, theme, root, scene) => {
    const head = textBox(theme, root, theme.display, BOX.x, BOX.y + 110, BOX.w, 300, v.text || "Follow for more", 104, 50, theme.text);
    if (theme.glow) glowText(head, theme, theme.accent, 1);
    const btn = place(el("div", "abs center", root), 300, BOX.y + 470, 480, 140);
    btn.style.borderRadius = "70px";
    font(btn, theme.display, 58);
    const label = el("span", "", btn, "Follow");
    // A drawn tick: not every font has the check mark character.
    const tick = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    tick.setAttribute("viewBox", "0 0 24 24");
    tick.setAttribute("width", "52");
    tick.setAttribute("height", "52");
    tick.style.marginLeft = "14px";
    tick.style.display = "none";
    const mark = document.createElementNS(tick.namespaceURI, "path");
    mark.setAttribute("d", "M4 12.5l5 5L20 6.5");
    mark.setAttribute("fill", "none");
    mark.setAttribute("stroke", "currentColor");
    mark.setAttribute("stroke-width", "3.2");
    mark.setAttribute("stroke-linecap", "round");
    mark.setAttribute("stroke-linejoin", "round");
    tick.appendChild(mark);
    btn.appendChild(tick);
    const burst = Array.from({ length: 14 }, (_, i) => {
      const dot = place(el("div", "abs", root), 532, BOX.y + 532, 16, 16);
      dot.style.borderRadius = "50%";
      dot.style.background = [theme.accent, theme.accent2, theme.accent3][i % 3];
      dot._a = (i / 14) * Math.PI * 2;
      return dot;
    });
    const hand = emoji(root, "👆", 150);
    place(hand, 560, BOX.y + 560, 150, 150);
    const at = beatsOf(scene, 1)[0];
    return (t) => {
      arrive(head, t, scene.start + 0.05);
      const pb = ease.back(prog(t, scene.start + 0.3, 0.5));
      const tapped = t >= at;
      const press = t >= at && t < at + 0.16 ? 0.92 : 1;
      btn.style.background = tapped ? (theme.glow ? "rgba(255,255,255,0.12)" : theme.card) : theme.glow ? theme.accent : theme.accent;
      btn.style.color = tapped ? theme.text : theme.glow ? theme.ink : theme.card;
      btn.style.border = theme.glow ? `4px solid ${theme.accent}` : `5px solid ${theme.ink}`;
      btn.style.boxShadow = theme.glow ? `0 0 50px ${theme.accent}88` : `8px 8px 0 ${theme.ink}`;
      label.textContent = tapped ? "Following" : "Follow";
      tick.style.display = tapped ? "inline-block" : "none";
      show(btn, clamp(pb), `scale(${lerp(0.5, 1, pb) * press})`);
      const ph = prog(t, scene.start + 0.5, at - scene.start - 0.5);
      const hx = lerp(260, 0, ease.out(ph));
      const hy = lerp(420, 0, ease.out(ph)) + (press < 1 ? 12 : 0);
      show(hand, clamp(ph * 3) * (1 - prog(t, at + 0.6, 0.3)), `translate(${hx}px, ${hy}px)`);
      burst.forEach((d) => {
        const p = prog(t, at, 0.7);
        const r = ease.out(p) * 260;
        show(d, p > 0 && p < 1 ? 1 - p : 0, `translate(${Math.cos(d._a) * r}px, ${Math.sin(d._a) * r}px)`);
      });
    };
  };

  // ---- characters -------------------------------------------------------------------
  // A cartoon rig drawn in SVG, seen from the side: the body leans and bobs, limbs are two
  // segments turned at the shoulder/elbow and hip/knee (angles from hanging straight down,
  // positive towards where the character faces), the face shows a feeling. Every pose is a
  // function of time, like everything else here.

  const SVGNS = "http://www.w3.org/2000/svg";
  const FLOOR = 1180; // where feet stand in a scene
  const BASE = 1.15; // an adult's size on the stage
  const RIG = { hipY: -250, shoulderY: -418, headY: -515, headR: 72, thigh: 125, shin: 125, upper: 100, fore: 92 };
  const SKINS = ["#F3CFAE", "#E6B48A", "#C98E62", "#8F5B3C", "#F7DCC3", "#D9A27A"];
  const HAIRS = ["#2B1D14", "#4A2E1B", "#6B4226", "#161616", "#8A4B2A", "#C9973F"];
  const SHIRTS = ["#FF7A59", "#4F86F7", "#2BB673", "#F7B500", "#9B5DE5", "#E84393", "#00A8A8"];
  const PANTS = ["#2F3A56", "#3B3B3B", "#5B4B3A", "#1F4E79", "#4B3F72"];
  // How long an entrance takes (timeline.py ENTRANCES) and how far off it starts.
  const ENTRANCES = { walk_in: [1.6, 420, 0.9], run_in: [0.9, 560, 0.5] };
  const WALK_OUT = 1.6;

  function svg(tag, attrs, parent) {
    const node = document.createElementNS(SVGNS, tag);
    for (const k in attrs) node.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(node);
    return node;
  }

  function shade(hex, by) {
    const n = parseInt(hex.slice(1), 16);
    const f = (c) => Math.max(0, Math.min(255, Math.round(c * (1 - by))));
    return `rgb(${f(n >> 16)},${f((n >> 8) & 255)},${f(n & 255)})`;
  }

  function lookOf(member, index) {
    const h = hash(member.name || "someone");
    const look = member.look || "man";
    const old = look.startsWith("old");
    const kid = look === "boy" || look === "girl";
    const female = look === "woman" || look === "girl" || look === "old woman";
    return {
      skin: SKINS[h % SKINS.length],
      hair: old ? "#D8D8D8" : HAIRS[(h >>> 3) % HAIRS.length],
      shirt: SHIRTS[(index * 3 + (h >>> 5)) % SHIRTS.length],
      pants: PANTS[(h >>> 7) % PANTS.length],
      hairStyle: old && !female ? "bald" : female ? (kid ? "pigtails" : h & 1 ? "long" : "bun") : kid ? "spiky" : "short",
      scale: kid ? 0.74 : old ? 0.95 : 1,
      glasses: old,
      skirt: female && !kid && (h & 2) === 0,
      stoop: old ? 3 : 0,
      blink: (h % 97) / 31,
    };
  }

  const HAIR_PATHS = {
    short: "M -72 6 C -84 -62 -30 -98 12 -94 C 58 -90 84 -58 70 -20 C 50 -42 18 -50 -12 -46 C -44 -42 -62 -22 -62 8 Z",
    spiky: "M -72 6 C -80 -40 -64 -70 -44 -82 L -30 -104 L -12 -86 L 6 -108 L 22 -88 L 44 -100 L 52 -76 C 74 -66 84 -44 72 -20 C 50 -42 18 -50 -12 -46 C -44 -42 -62 -22 -62 8 Z",
    long: "M -74 10 C -86 -64 -30 -100 14 -96 C 60 -92 86 -58 72 -18 C 52 -40 20 -48 -10 -44 C -40 -40 -58 -20 -60 10 Z",
    bun: "M -72 8 C -84 -62 -30 -98 12 -94 C 58 -90 84 -58 70 -20 C 50 -42 18 -50 -12 -46 C -44 -42 -62 -22 -62 8 Z",
    pigtails: "M -72 8 C -84 -62 -30 -98 12 -94 C 58 -90 84 -58 70 -20 C 50 -42 18 -50 -12 -46 C -44 -42 -62 -22 -62 8 Z",
    bald: "M -70 -6 C -76 14 -70 34 -56 44 L -44 30 C -54 18 -56 4 -54 -12 Z",
  };

  function limb(x0, y0, a1, l1, a2, l2) {
    const r1 = (a1 * Math.PI) / 180;
    const r2 = ((a1 + a2) * Math.PI) / 180;
    const x1 = x0 + l1 * Math.sin(r1);
    const y1 = y0 + l1 * Math.cos(r1);
    return [x1, y1, x1 + l2 * Math.sin(r2), y1 + l2 * Math.cos(r2)];
  }

  // The parts of one character, built once; draw(p) places them for pose p.
  function makeCharacter(look, theme, parent) {
    // Stick figure style: black lines, a white round head with the face to the front, hair
    // only where it says who someone is; the face colours with the mood.
    const stick = Boolean(theme.stick);
    if (stick) look = { ...look, shirt: "#111111", pants: "#111111", skin: "#111111", hair: "#111111" };
    const root = svg("g", {}, parent);
    const outline = theme.glow ? theme.accent : theme.ink;
    const lineW = theme.story ? 0 : theme.glow ? 3 : 4;
    const stroke = (el) => {
      if (lineW) {
        el.setAttribute("stroke", outline);
        el.setAttribute("stroke-width", String(lineW));
      }
      return el;
    };
    const shadow = svg("ellipse", { cx: 0, cy: 6, rx: 80, ry: 14, fill: stick ? "rgba(0,0,0,0)" : "rgba(0,0,0,0.16)" }, root);
    const body = svg("g", {}, root);
    const limbParts = (color, w, back) => {
      const g = svg("g", {}, body);
      const a = svg("line", { stroke: stick ? (back ? "#2b2b2b" : "#111111") : back ? shade(color, 0.18) : color, "stroke-width": stick ? 7 : w, "stroke-linecap": "round" }, g);
      return { g, a };
    };
    // Back limbs first, then the body, then the front limbs.
    const legB = { up: limbParts(look.pants, 38, true), low: limbParts(look.pants, 34, true) };
    const shoeB = svg("ellipse", { rx: stick ? 0 : 28, ry: stick ? 0 : 13, fill: shade("#3a3a3a", 0.25) }, body);
    const armB = { up: limbParts(look.shirt, 30, true), low: limbParts(look.skin, 25, true) };
    const handB = svg("circle", { r: stick ? 0 : 15, fill: shade(look.skin, 0.12) }, body);
    const longHair = look.hairStyle === "long" ? svg("ellipse", { rx: 70, ry: 98, fill: look.hair }, body) : null;
    const torso = stick
      ? svg("rect", { x: -3.5, y: RIG.shoulderY - 28, width: 7, height: 200, rx: 3.5, fill: "#111111" }, body)
      : stroke(svg("rect", { x: -56, y: RIG.shoulderY - 28, width: 112, height: 214, rx: 48, fill: look.shirt }, body));
    const skirt = look.skirt && !stick ? stroke(svg("path", { fill: shade(look.shirt, 0.15) }, body)) : null;
    const legF = { up: limbParts(look.pants, 38, false), low: limbParts(look.pants, 34, false) };
    const shoeF = svg("ellipse", { rx: stick ? 0 : 28, ry: stick ? 0 : 13, fill: "#3a3a3a" }, body);
    const head = svg("g", {}, body);
    const bun = look.hairStyle === "bun" ? svg("circle", { cx: -42, cy: -70, r: 30, fill: look.hair }, head) : null;
    const tail = look.hairStyle === "pigtails" ? svg("ellipse", { cx: -74, cy: 14, rx: 22, ry: 30, fill: look.hair }, head) : null;
    const skull = stick
      ? svg("circle", { cx: 0, cy: 0, r: RIG.headR, fill: "#ffffff", stroke: "#111111", "stroke-width": 6 }, head)
      : stroke(svg("circle", { cx: 0, cy: 0, r: RIG.headR, fill: look.skin }, head));
    if (!stick) svg("circle", { cx: -38, cy: 4, r: 13, fill: shade(look.skin, 0.1) }, head); // ear
    const hasHair = !stick || ["long", "bun", "pigtails"].includes(look.hairStyle);
    if (hasHair) stroke(svg("path", { d: HAIR_PATHS[look.hairStyle] || HAIR_PATHS.short, fill: look.hair }, head));
    // The face, turned to the front for stick figures.
    const face = svg("g", stick ? { transform: "translate(-27 2)" } : {}, head);
    const ink = stick ? "#111111" : "#1d1d1d";
    const blush = svg("circle", { cx: 42, cy: 20, r: 13, fill: "#FF7F7F", opacity: 0 }, face);
    const eyeB = svg("ellipse", { cx: 12, cy: -6, rx: 8, ry: 11, fill: ink }, face);
    const eyeF = svg("ellipse", { cx: 42, cy: -6, rx: 8.5, ry: 11.5, fill: ink }, face);
    const browB = svg("line", { x1: 2, y1: -26, x2: 22, y2: -26, stroke: stick ? ink : shade(look.hair, 0.1), "stroke-width": 6, "stroke-linecap": "round" }, face);
    const browF = svg("line", { x1: 32, y1: -27, x2: 54, y2: -27, stroke: stick ? ink : shade(look.hair, 0.1), "stroke-width": 6, "stroke-linecap": "round" }, face);
    if (!stick) svg("path", { d: "M 64 2 Q 74 10 63 16", fill: "none", stroke: shade(look.skin, 0.28), "stroke-width": 4, "stroke-linecap": "round" }, head); // nose
    const mouth = svg("path", { fill: "none", stroke: stick ? ink : "#5b2b22", "stroke-width": 5, "stroke-linecap": "round" }, face);
    const mouthOpen = svg("ellipse", { cx: 32, cy: 32, rx: 11, ry: 4, fill: stick ? ink : "#5b2b22" }, face);
    if (look.glasses) {
      for (const cx of [12, 42]) svg("circle", { cx, cy: -6, r: 15, fill: "none", stroke: "#333", "stroke-width": 4 }, face);
    }
    const tears = [0, 1].map(() => svg("ellipse", { rx: 5, ry: 8, fill: "#5DADEC", opacity: 0 }, face));
    const sweat = svg("ellipse", { cx: 56, cy: -44, rx: 6, ry: 10, fill: "#7FC8F8", opacity: 0 }, face);
    const TINTS = { angry: "#F28B82", sad: "#9ED8F5", scared: "#9ED8F5" };
    const armF = { up: limbParts(look.shirt, 30, false), low: limbParts(look.skin, 25, false) };
    const handF = svg("circle", { r: 15, fill: look.skin }, body);

    const setLine = (part, x1, y1, x2, y2) => {
      part.a.setAttribute("x1", x1.toFixed(1));
      part.a.setAttribute("y1", y1.toFixed(1));
      part.a.setAttribute("x2", x2.toFixed(1));
      part.a.setAttribute("y2", y2.toFixed(1));
    };
    const placeLimb = (parts, x0, y0, a1, l1, a2, l2) => {
      const [x1, y1, x2, y2] = limb(x0, y0, a1, l1, a2, l2);
      setLine(parts.up, x0, y0, x1, y1);
      setLine(parts.low, x1, y1, x2, y2);
      return [x2, y2];
    };

    const MOUTHS = {
      happy: "M 18 26 Q 32 42 48 24",
      sad: "M 20 36 Q 32 24 46 36",
      angry: "M 20 34 Q 33 28 46 34",
      neutral: "M 22 30 L 44 30",
      thinking: "M 26 32 Q 36 30 46 26",
      scared: "M 20 32 Q 26 26 32 32 Q 38 38 46 30",
      surprised: "M 26 30 L 40 30",
    };
    const BROWS = {
      // [back dy, back angle, front dy, front angle]
      neutral: [0, 0, 0, 0],
      happy: [-4, -6, -4, 6],
      sad: [2, -16, 2, 16],
      angry: [4, 18, 4, -18],
      surprised: [-12, -4, -12, 4],
      scared: [-8, -14, -8, 14],
      thinking: [0, 0, -9, -10],
    };

    return {
      root,
      // p: {x, y, dir, scale, lean, bob, tilt, armF, armB, legF, legB, sitY, mood, open, blink, look, tear, sweat}
      draw(p) {
        root.setAttribute("transform", `translate(${p.x.toFixed(1)} ${(FLOOR + p.y).toFixed(1)}) scale(${(p.dir * p.scale).toFixed(4)} ${p.scale.toFixed(4)})`);
        const lift = p.bob || 0;
        const hipY = RIG.hipY + (p.sitY || 0) + lift;
        shadow.setAttribute("rx", String(80 * Math.max(0.5, 1 + lift / 240)));
        body.setAttribute("transform", `rotate(${(p.lean || 0).toFixed(2)} 0 ${hipY.toFixed(1)})`);
        const fB = placeLimb(legB, -6, hipY, p.legB[0], RIG.thigh, -p.legB[1], RIG.shin);
        const fF = placeLimb(legF, 8, hipY, p.legF[0], RIG.thigh, -p.legF[1], RIG.shin);
        shoeB.setAttribute("cx", (fB[0] + 10).toFixed(1));
        shoeB.setAttribute("cy", (fB[1] + 4).toFixed(1));
        shoeF.setAttribute("cx", (fF[0] + 10).toFixed(1));
        shoeF.setAttribute("cy", (fF[1] + 4).toFixed(1));
        const shoulderY = RIG.shoulderY + (p.sitY || 0) + lift + (p.shrug || 0);
        torso.setAttribute("y", String(RIG.shoulderY - 28 + (p.sitY || 0) + lift));
        if (skirt) {
          const y = hipY - 20;
          skirt.setAttribute("d", `M -56 ${y} L 56 ${y} L 70 ${y + 90} L -70 ${y + 90} Z`);
        }
        const hB = placeLimb(armB, -6, shoulderY, p.armB[0], RIG.upper, p.armB[1], RIG.fore);
        const hF = placeLimb(armF, 10, shoulderY, p.armF[0], RIG.upper, p.armF[1], RIG.fore);
        handB.setAttribute("cx", hB[0].toFixed(1));
        handB.setAttribute("cy", hB[1].toFixed(1));
        handF.setAttribute("cx", hF[0].toFixed(1));
        handF.setAttribute("cy", hF[1].toFixed(1));
        const headY = RIG.headY + (p.sitY || 0) + lift + (p.shrug || 0) * 0.5;
        head.setAttribute("transform", `translate(4 ${headY.toFixed(1)}) rotate(${(p.tilt || 0).toFixed(2)})`);
        if (longHair) {
          longHair.setAttribute("cx", "-22");
          longHair.setAttribute("cy", String(headY + 40));
        }
        const mood = p.mood || "neutral";
        const [bdy, ba, fdy, fa] = BROWS[mood] || BROWS.neutral;
        browB.setAttribute("transform", `translate(0 ${bdy}) rotate(${ba} 12 -26)`);
        browF.setAttribute("transform", `translate(0 ${fdy}) rotate(${fa} 43 -27)`);
        const eyeRy = p.blink ? 1.6 : mood === "surprised" || mood === "scared" ? 13.5 : mood === "happy" ? 7 : 11;
        const [lx, ly] = p.look || [0, 0];
        eyeB.setAttribute("ry", String(eyeRy));
        eyeF.setAttribute("ry", String(eyeRy + 0.5));
        eyeB.setAttribute("cx", String(12 + lx));
        eyeF.setAttribute("cx", String(42 + lx));
        eyeB.setAttribute("cy", String(-6 + ly));
        eyeF.setAttribute("cy", String(-6 + ly));
        blush.setAttribute("opacity", mood === "happy" && !stick ? "0.35" : "0");
        if (stick) skull.setAttribute("fill", TINTS[mood] || "#ffffff");
        const open = p.open || (mood === "surprised" ? 0.8 : 0);
        if (open > 0.05) {
          mouth.setAttribute("d", "");
          mouthOpen.setAttribute("ry", (3 + 11 * open).toFixed(1));
          mouthOpen.setAttribute("opacity", "1");
        } else {
          mouth.setAttribute("d", MOUTHS[mood] || MOUTHS.neutral);
          mouthOpen.setAttribute("opacity", "0");
        }
        tears.forEach((drop, i) => {
          const phase = ((p.tear || 0) + i * 0.5) % 1;
          drop.setAttribute("cx", String(40 + i * 4));
          drop.setAttribute("cy", (6 + phase * 60).toFixed(1));
          drop.setAttribute("opacity", p.tear ? (1 - phase).toFixed(2) : "0");
        });
        sweat.setAttribute("opacity", p.sweat ? "0.9" : "0");
        // Where the hand and the head's top are once the body leans (it turns at the hip).
        const a = ((p.lean || 0) * Math.PI) / 180;
        const turn = ([x, y]) => [x * Math.cos(a) - (y - hipY) * Math.sin(a), hipY + x * Math.sin(a) + (y - hipY) * Math.cos(a)];
        return { hand: turn(hF), backHand: turn(hB), headTop: turn([4, headY - RIG.headR])[1] };
      },
    };
  }

  // A pose at local time lt (seconds into the action): limb angles, lean, bob and face.
  function pose(action, mood, lt, speaking, blinkAt) {
    const p = { lean: 0, bob: 0, tilt: 0, armF: [6, 12], armB: [-6, 12], legF: [3, 2], legB: [-3, 2], mood, open: 0, look: [0, 0] };
    const breathe = Math.sin((lt * 2 * Math.PI) / 3.2);
    p.armF = [6 + 2 * breathe, 12];
    p.armB = [-6 - 2 * breathe, 12];
    p.bob = 1.5 * breathe;
    const walk = (period, swing, knee, arm) => {
      const ph = (lt * 2 * Math.PI) / period;
      p.legF = [swing * Math.sin(ph), Math.max(0, knee * Math.sin(ph - 1.3))];
      p.legB = [swing * Math.sin(ph + Math.PI), Math.max(0, knee * Math.sin(ph + Math.PI - 1.3))];
      p.armF = [-arm * Math.sin(ph), 18 + 10 * Math.sin(ph)];
      p.armB = [arm * Math.sin(ph), 18 - 10 * Math.sin(ph)];
      p.bob = -5 * Math.abs(Math.cos(ph));
    };
    switch (action) {
      case "walking":
        walk(0.9, 24, 40, 22);
        break;
      case "running":
        walk(0.5, 40, 75, 42);
        p.armF[1] = 85;
        p.armB[1] = 85;
        p.lean = 9;
        break;
      case "wave": {
        const w = Math.sin(lt * 2 * Math.PI * 2.6);
        p.armF = [158, 28 + 24 * w];
        p.mood = mood === "neutral" ? "happy" : mood;
        break;
      }
      case "point":
        p.armF = [88, 4];
        p.look = [4, 0];
        break;
      case "think":
        p.armF = [38, 128];
        p.tilt = -7;
        p.look = [2, -5];
        p.mood = mood === "neutral" ? "thinking" : mood;
        break;
      case "celebrate": {
        const j = Math.abs(Math.sin(lt * Math.PI * 1.7));
        p.armF = [166 + 6 * Math.sin(lt * 9), 8];
        p.armB = [-166 - 6 * Math.sin(lt * 9), -8];
        p.bob = -58 * j;
        p.legF = [10 * j, 30 * j];
        p.legB = [-6 * j, 34 * j];
        p.mood = "happy";
        p.open = 0.7;
        break;
      }
      case "sad":
      case "cry":
        p.lean = 3;
        p.tilt = 14;
        p.armF = [2, 4];
        p.armB = [-2, 4];
        p.look = [0, 4];
        p.mood = "sad";
        p.bob = 0.8 * breathe;
        if (action === "cry") p.tear = (lt * 1.4) % 1;
        break;
      case "shocked": {
        const kick = Math.max(0, 1 - lt / 0.35);
        p.armF = [118, 40];
        p.armB = [-112, -40];
        p.lean = -7;
        p.bob = -24 * Math.sin(Math.min(1, lt / 0.35) * Math.PI) * (kick > 0 ? 1 : 0);
        p.mood = "surprised";
        p.sweat = true;
        break;
      }
      case "angry": {
        p.armF = [16, 70];
        p.armB = [-16, 70];
        p.lean = 4;
        p.mood = "angry";
        p.bob = Math.sin(lt * 40) * 1.5;
        break;
      }
      case "shrug":
        p.armF = [22, 92];
        p.armB = [-14, 92];
        p.shrug = -10;
        p.tilt = 6;
        p.mood = mood === "neutral" ? "thinking" : mood;
        break;
      case "sit":
        p.sitY = 100;
        p.legF = [92, 92];
        p.legB = [86, 90];
        p.armF = [24, 58];
        p.armB = [18, 58];
        break;
      case "read":
        p.armF = [34, 96];
        p.armB = [26, 96];
        p.tilt = 12;
        p.look = [2, 5];
        break;
      case "give":
        p.armF = [82, 8];
        p.look = [4, 0];
        p.mood = mood === "neutral" ? "happy" : mood;
        break;
      case "talk": {
        const g = Math.sin(lt * 2 * Math.PI * 0.9);
        p.armF = [32 + 12 * g, 70 + 22 * g];
        break;
      }
      default:
        break;
    }
    if (speaking) p.open = Math.max(p.open, speaking);
    // A blink every few seconds, each character on its own rhythm.
    const cycle = (lt + blinkAt) % 3.4;
    p.blink = cycle < 0.12 && action !== "celebrate";
    return p;
  }

  // How open the mouth is while `words` are spoken (talking along with its own line).
  function mouthAt(words, t) {
    for (const w of words || []) {
      if (t >= w.start && t < w.end) {
        const k = (t - w.start) / Math.max(0.08, w.end - w.start);
        return 0.25 + 0.55 * Math.abs(Math.sin(k * Math.PI * Math.max(1, Math.round((w.end - w.start) / 0.14))));
      }
    }
    return 0;
  }

  // ---- settings (where a scene happens) ------------------------------------------------

  const SCENE_PALETTES = {
    story: { sky: "#BFE3FF", sky2: "#E6F4FF", wall: "#FCE6D2", floor: "#EAC9A6", line: null, wood: "#B9835A", leaf: "#59B36B", leaf2: "#3E9A55", glass: "#D7EEFF", building: ["#F6B8A0", "#A9C8F0", "#F7D488", "#B8E0C2", "#D6B8F0"], window: "#FFF6D6", night: "#1D2450", board: "#2F6B4F", curtain: "#C8283C", grass: "#8ED081", sun: "#FFD45C" },
    paper: { sky: "#E9E4D8", sky2: "#F5EFE3", wall: "#F1EADB", floor: "#E2D6BF", line: "#1f1c18", wood: "#C9A27A", leaf: "#9DBF8C", leaf2: "#86AD76", glass: "#E6EEF0", building: ["#E8DCC6", "#D9E1E4", "#EADBB8", "#DCE5D3", "#E3D9E8"], window: "#FFFFFF", night: "#3A3F5C", board: "#5E7A64", curtain: "#B4534A", grass: "#C8D6A8", sun: "#F2C14E" },
    stick: { sky: "#FFFFFF", sky2: "#FFFFFF", wall: "#FFFFFF", floor: "#EFEBE9", line: "#111111", wood: "#8D6E63", leaf: "#FFFFFF", leaf2: "#FFFFFF", glass: "#FFFFFF", building: ["#FFFFFF"], window: "#FFFFFF", night: "#FFFFFF", board: "#2E4D3A", curtain: "#8D6E63", grass: "#FFFFFF", sun: "#FFFFFF" },
    glow: { sky: "#0B1033", sky2: "#141B4D", wall: "#121842", floor: "#0A0E2A", line: "#38e1ff", wood: "#2A2F6A", leaf: "#1E6F5C", leaf2: "#145244", glass: "#1C2A66", building: ["#1A2058", "#22296B", "#1B2560", "#252C70", "#1D2464"], window: "#38e1ff", night: "#070A22", board: "#13324A", curtain: "#5A1B4A", grass: "#103A3A", sun: "#ff4fd8" },
  };

  function setting(name, theme, parent, seed) {
    const P = SCENE_PALETTES[theme.palette] || SCENE_PALETTES.story;
    const g = svg("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}` }, parent);
    g.style.position = "absolute";
    g.style.left = "0";
    g.style.top = "0";
    const r = rng(seed);
    const lineAttrs = P.line ? { stroke: P.line, "stroke-width": theme.glow ? 3 : 4, "stroke-opacity": theme.glow ? 0.7 : 1 } : {};
    const rect = (x, y, w, h, fill, extra = {}) => svg("rect", { x, y, width: w, height: h, fill, ...lineAttrs, ...extra }, g);
    const circle = (cx, cy, rad, fill, extra = {}) => svg("circle", { cx, cy, r: rad, fill, ...lineAttrs, ...extra }, g);
    const sky = (top, bottom) => {
      const id = `sky${seed}`;
      const defs = svg("defs", {}, g);
      const grad = svg("linearGradient", { id, x1: 0, y1: 0, x2: 0, y2: 1 }, defs);
      svg("stop", { offset: "0", "stop-color": top }, grad);
      svg("stop", { offset: "1", "stop-color": bottom }, grad);
      svg("rect", { x: 0, y: 0, width: W, height: FLOOR, fill: `url(#${id})` }, g);
    };
    const floor = (color) => {
      svg("rect", { x: 0, y: FLOOR, width: W, height: H - FLOOR, fill: color }, g);
      if (P.line) svg("line", { x1: 0, y1: FLOOR, x2: W, y2: FLOOR, ...lineAttrs }, g);
    };
    const tree = (x, s) => {
      rect(x - 14 * s, FLOOR - 230 * s, 28 * s, 230 * s, P.wood);
      circle(x, FLOOR - 270 * s, 95 * s, P.leaf);
      circle(x - 60 * s, FLOOR - 220 * s, 70 * s, P.leaf2);
      circle(x + 62 * s, FLOOR - 228 * s, 66 * s, P.leaf2);
    };
    if (theme.stick) {
      stickSetting(name, g, r);
      return g;
    }
    switch (name) {
      case "street": {
        sky(P.sky, P.sky2);
        let x = -20;
        let i = 0;
        while (x < W) {
          const w = 170 + r() * 90;
          const h = 330 + r() * 260;
          rect(x, FLOOR - h, w, h, P.building[i % P.building.length]);
          for (let wy = FLOOR - h + 34; wy < FLOOR - 70; wy += 70) {
            for (let wx = x + 24; wx < x + w - 40; wx += 52) rect(wx, wy, 28, 38, P.window, { opacity: theme.glow ? 0.5 : 0.95 });
          }
          x += w + 8;
          i++;
        }
        floor(theme.glow ? "#0E1238" : "#C9C3BC");
        rect(0, FLOOR + 90, W, 14, theme.glow ? "#38e1ff" : "#ffffff", { opacity: 0.5 });
        break;
      }
      case "park":
        sky(P.sky, P.sky2);
        circle(860, 520, 70, P.sun, { opacity: 0.95 });
        svg("ellipse", { cx: 250, cy: FLOOR + 30, rx: 520, ry: 210, fill: P.leaf2, opacity: 0.55 }, g);
        svg("ellipse", { cx: 880, cy: FLOOR + 40, rx: 480, ry: 180, fill: P.leaf, opacity: 0.55 }, g);
        tree(150, 1.0);
        tree(960, 0.8);
        floor(P.grass);
        for (let i = 0; i < 26; i++) {
          const gx = r() * W;
          const gy = FLOOR + 40 + r() * 600;
          svg("path", { d: `M ${gx} ${gy} l 8 -26 l 8 26 l 8 -18 l 6 18`, fill: P.leaf2, opacity: 0.5 }, g);
        }
        break;
      case "office":
        sky(P.wall, P.wall);
        rect(620, 400, 330, 300, P.glass);
        for (let y = 420; y < 700; y += 26) svg("line", { x1: 620, y1: y, x2: 950, y2: y, stroke: theme.glow ? "#38e1ff" : "#ffffff", "stroke-opacity": 0.35, "stroke-width": 3 }, g);
        rect(560, FLOOR - 190, 440, 26, P.wood);
        rect(590, FLOOR - 164, 22, 164, P.wood);
        rect(950, FLOOR - 164, 22, 164, P.wood);
        rect(700, FLOOR - 330, 190, 130, theme.glow ? "#05060f" : "#2b2b2b", { rx: 10 });
        rect(712, FLOOR - 318, 166, 104, theme.glow ? "#38e1ff" : "#7FB3E6", { opacity: 0.6 });
        floor(P.floor);
        break;
      case "night":
        sky(P.night, theme.glow ? "#05060f" : "#2E3A6E");
        for (let i = 0; i < 70; i++) circle(r() * W, r() * 760 + 240, 1.5 + r() * 2.5, "#FFFFFF", { opacity: 0.4 + r() * 0.6, stroke: "none" });
        circle(820, 420, 70, "#FFF4C8", { stroke: "none" });
        circle(848, 404, 62, P.night, { stroke: "none" });
        for (const [x, w, h] of [[40, 240, 260], [300, 200, 200], [700, 300, 300]]) {
          rect(x, FLOOR - h, w, h, theme.glow ? "#0B0F2E" : "#1A2045");
          rect(x + 40, FLOOR - h + 60, 46, 56, "#FFD66B", { opacity: 0.9 });
        }
        floor(theme.glow ? "#05060f" : "#141936");
        break;
      case "school":
        sky(P.wall, P.wall);
        rect(160, 420, 760, 330, P.board, { rx: 12 });
        for (let i = 0; i < 4; i++) svg("path", { d: `M ${220 + i * 160} ${500 + (i % 2) * 90} q 40 -30 80 0 t 80 0`, fill: "none", stroke: "#ffffff", "stroke-opacity": 0.6, "stroke-width": 5 }, g);
        rect(140, 750, 800, 18, P.wood);
        floor(P.floor);
        break;
      case "shop":
        sky(P.wall, P.wall);
        for (let row = 0; row < 3; row++) {
          rect(80, 430 + row * 150, 920, 14, P.wood);
          for (let b = 0; b < 9; b++) rect(100 + b * 100, 430 + row * 150 - 70, 70, 70, P.building[(b + row) % P.building.length], { rx: 8 });
        }
        rect(520, FLOOR - 200, 480, 200, P.wood, { rx: 10 });
        floor(P.floor);
        break;
      case "stage":
        sky(theme.glow ? "#05060f" : "#2A1E2E", theme.glow ? "#0B1033" : "#3B2A3F");
        svg("path", { d: `M 0 220 L 220 220 Q 180 700 230 ${FLOOR} L 0 ${FLOOR} Z`, fill: P.curtain }, g);
        svg("path", { d: `M ${W} 220 L ${W - 220} 220 Q ${W - 180} 700 ${W - 230} ${FLOOR} L ${W} ${FLOOR} Z`, fill: P.curtain }, g);
        svg("polygon", { points: `460,220 620,220 900,${FLOOR} 180,${FLOOR}`, fill: "#FFF3C4", opacity: 0.18 }, g);
        floor(theme.glow ? "#14102E" : "#6B4A35");
        break;
      case "plain":
        sky(P.sky2, P.sky2);
        floor(P.floor);
        break;
      default: {
        // room
        sky(P.wall, P.wall);
        rect(640, 430, 270, 270, P.glass, { rx: 6 });
        svg("line", { x1: 775, y1: 430, x2: 775, y2: 700, stroke: P.wood, "stroke-width": 10 }, g);
        svg("line", { x1: 640, y1: 565, x2: 910, y2: 565, stroke: P.wood, "stroke-width": 10 }, g);
        rect(170, 470, 190, 140, P.building[1], { rx: 6 });
        svg("polygon", { points: "185,600 245,520 300,600", fill: P.leaf }, g);
        circle(320, 505, 18, P.sun, { stroke: "none" });
        rect(0, FLOOR - 30, W, 30, shade(P.wall.startsWith("#") ? P.wall : "#cccccc", 0.12));
        rect(905, FLOOR - 110, 80, 110, P.building[0], { rx: 10 });
        circle(945, FLOOR - 150, 46, P.leaf);
        floor(P.floor);
        svg("ellipse", { cx: 540, cy: FLOOR + 28, rx: 400, ry: 42, fill: P.building[4], opacity: 0.5 }, g);
      }
    }
    return g;
  }

  // Stick figure style: a white page, a floor and a few black outlines; the people tell it.
  function stickSetting(name, g, r) {
    const line = { stroke: "#111111", "stroke-width": 5, "stroke-linejoin": "round", fill: "none" };
    const band = (color, h) => {
      svg("rect", { x: 0, y: FLOOR, width: W, height: h, fill: color }, g);
      svg("rect", { x: 0, y: FLOOR + h, width: W, height: H - FLOOR - h, fill: "#FFFFFF" }, g);
      svg("line", { x1: 0, y1: FLOOR, x2: W, y2: FLOOR, stroke: "#111111", "stroke-width": 4 }, g);
    };
    const walls = () => {
      svg("rect", { x: 0, y: 220, width: 46, height: FLOOR - 220, fill: "#6E5D55" }, g);
      svg("rect", { x: W - 46, y: 220, width: 46, height: FLOOR - 220, fill: "#6E5D55" }, g);
    };
    const tree = (x, s) => {
      svg("line", { x1: x, y1: FLOOR, x2: x, y2: FLOOR - 200 * s, ...line }, g);
      svg("circle", { cx: x, cy: FLOOR - 270 * s, r: 85 * s, ...line, fill: "#FFFFFF" }, g);
    };
    svg("rect", { x: 0, y: 0, width: W, height: FLOOR, fill: "#FFFFFF" }, g);
    switch (name) {
      case "street":
        for (const [x, w, h] of [[60, 200, 420], [300, 160, 300], [780, 220, 460]]) {
          svg("rect", { x, y: FLOOR - h, width: w, height: h, ...line, fill: "#FFFFFF" }, g);
          for (let wy = FLOOR - h + 40; wy < FLOOR - 80; wy += 90) svg("rect", { x: x + 34, y: wy, width: 40, height: 50, ...line, "stroke-width": 3 }, g);
        }
        band("#5B5B5B", 70);
        break;
      case "park":
        tree(170, 1);
        tree(930, 0.8);
        band("#8BC34A", 40);
        break;
      case "night":
        svg("circle", { cx: 840, cy: 430, r: 60, ...line }, g);
        for (let i = 0; i < 9; i++) svg("text", { x: 120 + r() * 840, y: 330 + r() * 300, "font-size": 34, fill: "#111111" }, g).textContent = "*";
        band("#3E3E3E", 40);
        break;
      case "office":
        svg("rect", { x: 600, y: FLOOR - 190, width: 380, height: 20, ...line, fill: "#A1887F" }, g);
        svg("line", { x1: 630, y1: FLOOR - 170, x2: 630, y2: FLOOR, ...line }, g);
        svg("line", { x1: 950, y1: FLOOR - 170, x2: 950, y2: FLOOR, ...line }, g);
        svg("rect", { x: 720, y: FLOOR - 320, width: 160, height: 120, ...line, fill: "#B0BEC5" }, g);
        walls();
        band("#8D6E63", 36);
        break;
      case "school":
        svg("rect", { x: 200, y: 440, width: 680, height: 300, ...line, fill: "#2E4D3A" }, g);
        walls();
        band("#8D6E63", 36);
        break;
      case "shop":
        svg("rect", { x: 560, y: FLOOR - 210, width: 420, height: 210, ...line, fill: "#BCAAA4" }, g);
        walls();
        band("#8D6E63", 36);
        break;
      case "stage":
        svg("rect", { x: 300, y: FLOOR - 160, width: 480, height: 160, ...line, fill: "#8D6E63" }, g);
        band("#6D4C41", 40);
        break;
      case "plain":
        band("#9E9E9E", 30);
        break;
      default:
        walls();
        band("#8D6E63", 36);
    }
  }

  // People standing around (scene.crowd): smaller, idle, behind the actors.
  function crowdOf(n, theme, parent, avoid, seed) {
    const r = rng(seed);
    const spots = [];
    for (let x = 90; x <= W - 90; x += 115) {
      if (avoid.every((a) => Math.abs(a - x) > 150)) spots.push(x);
    }
    const out = [];
    const count = Math.min(n, spots.length);
    for (let i = 0; i < count; i++) {
      const pick = spots.splice(Math.floor(r() * spots.length), 1)[0];
      const look = lookOf({ name: `extra${seed}-${i}`, look: ["man", "woman", "boy", "girl"][Math.floor(r() * 4)] }, i + 3);
      const rig = makeCharacter(look, theme, parent);
      out.push({ rig, x: pick, look, dir: pick < 540 ? 1 : -1, phase: r() * 3 });
    }
    return out;
  }

  // ---- the character scene -------------------------------------------------------------

  // Where the red note goes, pointing at the first actor: above its head, or beside it, never
  // over a speech bubble, a thought, another actor or the big object. A lone speaker's bubble
  // leans the way it looks to leave the other side free. Two actors with a line have no room
  // for both: the timeline then has the note shown first and the lines wait (talkFrom).
  function noteSpot(cast, piece, count, top, bubbleTop, scene) {
    const c = cast[0];
    const headTop = (a) => FLOOR - BASE * a.look.scale * 590;
    if (c.bubble && cast.length === 1) c.lean = c.dir * 140;
    const hx = c.home;
    const ht = headTop(c);
    const blocked = [];
    const first = scene.talkFrom != null;
    cast.forEach((a) => {
      if (first) {
        // shown before any line: nothing to keep clear of but the people
      } else if (a.bubble) {
        blocked.push({ x: clamp(a.home - 200 + (a.lean || 0), 40, W - 440), y: Math.max(bubbleTop, headTop(a) - 185), w: 400, h: 184 });
      } else if (a.thought) {
        blocked.push({ x: clamp(a.home + a.dir * 120 - 75, 40, W - 190), y: Math.max(bubbleTop, headTop(a) - 40), w: 150, h: 90 });
      }
      if (a !== c) blocked.push({ x: a.home - 110, y: headTop(a) - 10, w: 220, h: FLOOR - headTop(a) + 10 });
    });
    if (piece) blocked.push({ x: 790 + (count === 1 ? 0 : 80), y: FLOOR - 236, w: 230, h: 240 });
    const y = Math.max(top, ht - 150);
    const aboveRight = { x: clamp(hx - 40, 30, W - 450), y, w: 420, h: 84 };
    aboveRight.from = [clamp(hx + 150, aboveRight.x + 60, aboveRight.x + 400), y + 86];
    aboveRight.to = [hx + 52, ht + 14];
    aboveRight.mid = [aboveRight.from[0] + 10, (aboveRight.from[1] + aboveRight.to[1]) / 2 + 10];
    const aboveLeft = { x: clamp(hx - 380, 30, W - 450), y, w: 420, h: 84 };
    aboveLeft.from = [clamp(hx - 150, aboveLeft.x + 20, aboveLeft.x + 360), y + 86];
    aboveLeft.to = [hx - 52, ht + 14];
    aboveLeft.mid = [aboveLeft.from[0] - 10, (aboveLeft.from[1] + aboveLeft.to[1]) / 2 + 10];
    const side = (dir) => {
      const sy = Math.max(top, ht - 30);
      const w = Math.min(420, dir < 0 ? hx - 140 - 30 : W - 30 - (hx + 140));
      const r = { x: dir < 0 ? 30 : hx + 140, y: sy, w, h: 130 };
      if (dir < 0) r.x = hx - 140 - w;
      r.from = [dir < 0 ? r.x + w + 8 : r.x - 8, sy + 72];
      r.to = [hx + dir * 66, ht + 70];
      r.mid = [(r.from[0] + r.to[0]) / 2, Math.min(r.from[1], r.to[1]) - 24];
      return r;
    };
    const hits = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
    const free = (r) => r.w >= 220 && !blocked.some((b) => hits(r, b));
    const found = [aboveRight, aboveLeft, side(-c.dir), side(c.dir)].find(free) || aboveRight;
    return { ...found, until: first ? scene.talkFrom : Infinity };
  }

  SCENES.scene = (v, theme, root, scene, data) => {
    const castList = (data && data.cast) || [];
    const lookFor = (name) => {
      const i = Math.max(0, castList.findIndex((c) => (c.name || "").toLowerCase() === (name || "").toLowerCase()));
      return lookOf(castList[i] || { name, look: "man" }, i);
    };
    // A painted picture is the place itself; otherwise the place is drawn.
    if (!scene._picture) setting(v.setting || "room", theme, root, hash((v.setting || "room") + scene.start));
    const actors = (v.actors || []).slice(0, 2);
    if (!actors.length) actors.push({ name: "Sam", action: "idle", mood: "neutral" });
    const beats = beatsOf(scene, actors.length + 1);
    const stageSvg = svg("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}` }, root);
    stageSvg.style.position = "absolute";
    stageSvg.style.left = "0";
    stageSvg.style.top = "0";
    // A big object beside them (v.emoji) moves them over to make room.
    const piece = v.emoji ? emoji(root, v.emoji, 230) : null;
    const homes = piece ? (actors.length === 1 ? [400] : [250, 630]) : actors.length === 1 ? [540] : [330, 750];
    if (piece) place(piece, 790 + (actors.length === 1 ? 0 : 80), FLOOR - 236, 230, 240);
    const extras = v.crowd ? crowdOf(v.crowd, theme, stageSvg, piece ? [...homes, 905] : homes, hash(v.text + scene.start)) : [];
    const cast = actors.map((a, i) => {
      const look = lookFor(a.name);
      const chair = a.action === "sit" ? svg("g", {}, stageSvg) : null;
      if (chair) {
        const wood = (SCENE_PALETTES[theme.palette] || SCENE_PALETTES.story).wood;
        svg("rect", { x: -70, y: -160, width: 150, height: 22, rx: 8, fill: wood }, chair);
        svg("rect", { x: -64, y: -140, width: 16, height: 140, fill: shade(wood, 0.2) }, chair);
        svg("rect", { x: 56, y: -140, width: 16, height: 140, fill: shade(wood, 0.2) }, chair);
        svg("rect", { x: -78, y: -330, width: 20, height: 190, rx: 8, fill: wood }, chair);
      }
      const rig = makeCharacter(look, theme, stageSvg);
      // Two face each other; one alone faces the way its name says (for variety).
      const dir = actors.length === 2 ? (i === 0 ? 1 : -1) : hash(a.name || "x") % 2 ? 1 : -1;
      const prop = a.prop ? emoji(root, a.prop, Math.round(100 * look.scale * BASE)) : null;
      if (prop) prop.style.zIndex = "2";
      const bubble = a.says ? place(el("div", "abs fit center", root), 0, 0, 400, 150) : null;
      if (bubble) {
        font(bubble, theme.body, 46, theme.story || !theme.glow ? "#1f1c18" : "#05060f");
        bubble.textContent = a.says;
        bubble.style.background = "#ffffff";
        bubble.style.borderRadius = "40px";
        bubble.style.padding = "14px 26px";
        bubble.style.border = theme.story ? "none" : `4px solid ${theme.glow ? theme.accent : theme.ink}`;
        bubble.style.boxShadow = theme.story ? "0 10px 0 rgba(0,0,0,0.12)" : theme.glow ? `0 0 30px ${theme.accent}88` : `8px 8px 0 ${theme.ink}`;
        bubble._fit = [50, 28];
        const tail = el("div", "abs", root);
        bubble._tail = tail;
        tail.style.width = "34px";
        tail.style.height = "34px";
        tail.style.background = "#ffffff";
        tail.style.zIndex = "1";
        tail.style.borderRight = bubble.style.border;
        tail.style.borderBottom = bubble.style.border;
      }
      const thought = !a.says && a.action === "think" ? place(el("div", "abs center", root), 0, 0, 150, 90) : null;
      if (thought) {
        font(thought, theme.display, 54, "#1f1c18");
        thought.textContent = "...";
        thought.style.background = "#ffffff";
        thought.style.borderRadius = "50px";
        thought.style.boxShadow = "0 8px 0 rgba(0,0,0,0.12)";
      }
      return { a, look, rig, chair, dir, prop, bubble, thought, home: homes[i], at: beats[i] };
    });
    const bubbleAt = beats[actors.length];
    const sceneEnd = scene.end;
    // An optional caption ("Years later") as a pill under the title bar.
    const caption = v.text && !theme.stick ? textBox(theme, root, theme.body, 290, 352, 500, 64, v.text, 38, 24, theme.glow ? theme.ink : "#ffffff") : null;
    if (caption) {
      caption.style.background = theme.glow ? theme.accent : theme.ink;
      caption.style.borderRadius = "34px";
    }
    const bubbleTop = caption ? 440 : 340;
    // A red note with an arrow at the first actor ("40 days", "Skin peeling off").
    const spot = v.detail && cast[0] ? noteSpot(cast, piece, actors.length, caption ? 440 : 344, bubbleTop, scene) : null;
    // Lines wait for a note shown first (the timeline's call: no room for both).
    const talkFrom = scene.talkFrom != null ? scene.talkFrom : -Infinity;
    const note = spot ? textBox(theme, root, theme.stick ? theme.display : theme.body, spot.x, spot.y, spot.w, spot.h, v.detail, 64, 32, theme.stick ? "#E53935" : theme.accent) : null;
    const arrow = note ? svg("path", { fill: "none", stroke: theme.stick ? "#E53935" : theme.accent, "stroke-width": 6, "stroke-linecap": "round", "stroke-linejoin": "round" }, stageSvg) : null;
    return (t) => {
      cast.forEach((c, i) => {
        const lt = Math.max(0, t - c.at);
        let x = c.home;
        let action = c.a.action;
        let alpha = ease.out(prog(t, c.at - 0.15, 0.3));
        let dir = c.dir;
        if (ENTRANCES[action]) {
          const [len, far] = ENTRANCES[action];
          const p = prog(t, c.at, len);
          x = c.home - dir * far * (1 - ease.out(p));
          action = p < 1 ? (action === "run_in" ? "running" : "walking") : "idle";
          alpha = 1;
        } else if (action === "walk_out") {
          const p = prog(t, sceneEnd - WALK_OUT, WALK_OUT);
          if (p > 0) {
            dir = -c.dir;
            x = c.home + dir * 520 * ease.inOut(p);
            action = "walking";
          } else {
            action = "idle";
          }
        }
        // The mouth moves with the voice while this character's line is being said.
        const speaking = c.a.says && t >= Math.max(bubbleAt, talkFrom) ? mouthAt(scene.words, t) : action === "talk" ? mouthAt(scene.words, t) : 0;
        const p = pose(action, c.a.mood || "neutral", lt, speaking, c.look.blink + i);
        p.x = x;
        p.y = 0;
        p.dir = dir;
        p.scale = c.look.scale * BASE;
        p.lean = (p.lean || 0) + c.look.stoop;
        const at = c.rig.draw(p);
        c.rig.root.setAttribute("opacity", alpha.toFixed(3));
        if (c.chair) c.chair.setAttribute("transform", `translate(${x} ${FLOOR}) scale(${dir * p.scale} ${p.scale})`);
        const handX = x + dir * p.scale * (at.hand[0] + 4);
        const handY = FLOOR + p.scale * at.hand[1];
        const headTop = FLOOR + p.scale * at.headTop;
        if (c.prop) {
          // In hand only when the hands are low: up in the air it would cover the face.
          const holds = !["celebrate", "wave", "shocked", "think", "cry"].includes(action);
          c.prop.style.visibility = holds ? "visible" : "hidden";
          const size = parseFloat(c.prop.style.fontSize);
          place(c.prop, handX - size / 2, handY - size * 0.62, size, size);
          show(c.prop, alpha, action === "celebrate" ? `rotate(${Math.sin(t * 8) * 10}deg)` : "none");
        }
        if (c.bubble) {
          const bx = clamp(x - 200 + (c.lean || 0), 40, W - 440);
          const by = Math.max(bubbleTop, headTop - 185);
          place(c.bubble, bx, by, 400, 150);
          const pb = ease.back(prog(t, Math.max(bubbleAt, talkFrom), 0.35));
          show(c.bubble, clamp(pb), `scale(${lerp(0.5, 1, pb)})`);
          // The tail points down at the speaker's head, under the bubble.
          place(c.bubble._tail, clamp(x - 17, bx + 30, bx + 336), by + 132, 34, 34);
          show(c.bubble._tail, clamp(pb), `rotate(45deg) scale(${lerp(0.5, 1, pb)})`);
        }
        if (c.thought) {
          // Beside the head, in front of the face.
          place(c.thought, clamp(x + dir * 120 - 75, 40, W - 190), Math.max(bubbleTop, headTop - 40), 150, 90);
          const pt = ease.back(prog(t, Math.max(c.at + 0.4, talkFrom), 0.35));
          show(c.thought, clamp(pt), `scale(${lerp(0.4, 1, pt)})`);
        }
      });
      extras.forEach((x, i) => {
        const p = pose("idle", "neutral", t - scene.start + x.phase, 0, x.look.blink + i);
        p.x = x.x;
        p.y = theme.stick ? 0 : -40; // a line for a floor: everyone stands on it
        p.dir = x.dir;
        p.scale = x.look.scale * BASE * 0.78;
        x.rig.draw(p);
        x.rig.root.setAttribute("opacity", ease.out(prog(t, scene.start + 0.05 * i, 0.3)).toFixed(3));
      });
      if (piece) arrive(piece, t, scene.start + 0.3, 0, 60);
      if (note) {
        const { from, mid, to } = spot;
        const tip = Math.atan2(to[1] - mid[1], to[0] - mid[0]);
        const wing = (a) => `${(to[0] - 26 * Math.cos(tip + a)).toFixed(1)} ${(to[1] - 26 * Math.sin(tip + a)).toFixed(1)}`;
        arrow.setAttribute("d", `M ${from[0]} ${from[1]} Q ${mid[0]} ${mid[1]} ${to[0]} ${to[1]} M ${wing(0.5)} L ${to[0]} ${to[1]} L ${wing(-0.5)}`);
        // With nowhere free it shows first and makes way when the line starts.
        const pn = spot.until < Infinity
          ? ease.out(prog(t, scene.start + 0.3, 0.35)) * (1 - ease.out(prog(t, spot.until - 0.25, 0.25)))
          : ease.out(prog(t, Math.max(scene.start + 0.6, bubbleAt), 0.35));
        show(note, pn, `translateY(${(1 - pn) * 20}px)`);
        arrow.setAttribute("opacity", pn.toFixed(3));
      }
      if (caption) arrive(caption, t, scene.start + 0.2);
    };
  };


  // ---- Bold kinetic text: the narration itself, a few words at a time, slammed in -------

  SCENES.kinetic = (v, theme, root, scene) => {
    const flash = place(el("div", "abs", root), 0, 0, W, H);
    flash.style.background = theme.accent;
    const keys = new Set(
      [v.text, v.highlight, v.value, v.answer, ...(v.items || []).map((i) => i.text)]
        .join(" ")
        .split(/\s+/)
        .map(plain)
        .filter((w) => w.length > 3),
    );
    const chunks = [];
    let cur = [];
    for (const w of scene.words || []) {
      cur.push(w);
      const chars = cur.reduce((n, x) => n + x.text.length, 0);
      if (cur.length >= 3 || chars >= 13 || /[.!?,;:]$/.test(w.text)) {
        chunks.push(cur);
        cur = [];
      }
    }
    if (cur.length) chunks.push(cur);
    if (!chunks.length) chunks.push([{ text: v.text || v.value || "", start: scene.start, end: scene.end }]);
    const colors = [theme.text, theme.accent, theme.text, theme.accent3];
    const boxes = chunks.map((words, i) => {
      const box = place(el("div", "abs fit center", root), BOX.x - 20, BOX.y + 40, BOX.w + 40, BOX.h);
      font(box, theme.display, 230, colors[i % colors.length]);
      box.style.flexWrap = "wrap";
      box.style.lineHeight = "0.95";
      box.style.textTransform = "uppercase";
      const line = el("div", "line", box);
      words.forEach((w, k) => {
        if (k) line.appendChild(document.createTextNode(" "));
        const span = el("span", "word", line, w.text.replace(/[,;:]$/, ""));
        if (keys.has(plain(w.text))) {
          span.style.color = theme.accent2;
          span.style.textShadow = `0 0 40px ${theme.accent2}66`;
        }
      });
      box._fit = [230, 90];
      return { box, start: words[0].start, end: words[words.length - 1].end };
    });
    return (t) => {
      let shown = -1;
      boxes.forEach((b, i) => {
        const next = boxes[i + 1];
        const until = next ? next.start : scene.end + 0.3;
        const on = t >= b.start - 0.04 && t < until;
        b.box.style.display = on ? "flex" : "none";
        if (!on) return;
        shown = i;
        const p = prog(t, b.start - 0.04, 0.14);
        const e = ease.out(p);
        const tilt = (i % 2 ? 1 : -1) * 2.5 * (1 - e);
        show(b.box, clamp(p * 2), `scale(${lerp(1.45, 1, e)}) rotate(${tilt}deg)`);
      });
      const at = shown >= 0 ? boxes[shown].start : scene.start;
      const f = prog(t, at - 0.04, 0.25);
      show(flash, f < 1 ? 0.16 * (1 - f) : 0);
    };
  };

  // ---- the World map style ------------------------------------------------------------
  // A camera flies over a satellite picture of the Earth (animate/maps.py tiles, an
  // equirectangular picture shown with the camera's own latitude squeeze), lights each
  // country as it is named and pins a card on it for every thing it names. The camera is a
  // pure function of t, worked out from the scenes' beats; tiles a frame needs are loaded
  // before it is called done (seek returns a promise the renderer waits for).

  const RAD = Math.PI / 180;
  const CARD = { w: 376, h: 470, y: 790, label: 56 }; // y: where a new card's centre sits
  const OCEAN = "#00293b";
  // A reel's look (Plan.look), as the reference page has them: white picture cards; gold
  // horror (blood-red countries, photos in gold frames, gold names); neon labels (the dish
  // cut out on the map, its name glowing in a colour of its own).
  const LOOK_CARDS = {
    cards: { w: 376, h: 470, gap: 14, label: 56 },
    horror: { w: 420, h: 300, gap: 10, label: 66 },
    neon: { w: 440, h: 360, gap: 2, label: 76 },
  };
  const NEON = ["#7CFF6B", "#FF6EC7", "#5CE1FF", "#FFE45C", "#B98CFF", "#FF9F43"];
  // Where a caption may sit (its top), in order: its usual place low on the screen, then
  // lower, higher, lowest, as cards leave room.
  const CAPTION_BANDS = [1305, 1470, 1140, 1620];
  const GOLD = "#F6C453";
  const GOLD_EDGE = "#D9A441";
  const BLOOD = "#8a0d12";
  const mapLookOf = (data) => (data && LOOK_CARDS[data.look] ? data.look : "cards");
  let mapState = null; // the loaded reel's map (camera, projection) while scenes are built
  let building = null; // load()'s pending pictures and options, for the scene builders

  const wrapLon = (lon) => ((((lon + 180) % 360) + 360) % 360) - 180;

  function project(cam) {
    const s = W / cam.w; // pixels per degree of arc
    const k = Math.max(0.05, Math.cos(cam.lat * RAD));
    return {
      s,
      k,
      x: (lon) => W / 2 + wrapLon(lon - cam.lon) * k * s,
      y: (lat) => H / 2 - (lat - cam.lat) * s,
    };
  }

  // The camera between two views: centre and zoom eased together, and on a long flight a
  // pull back in the middle so the way there is seen.
  function between(a, b, p, fly) {
    const e = ease.inOut(clamp(p));
    let dlon = b.lon - a.lon;
    if (dlon > 180) dlon -= 360;
    if (dlon < -180) dlon += 360;
    let lw = lerp(Math.log(a.w), Math.log(b.w), e);
    if (fly) {
      const k = Math.cos(((a.lat + b.lat) / 2) * RAD);
      const dist = Math.hypot(dlon * k, b.lat - a.lat);
      lw += Math.log(1 + dist / Math.max(a.w, b.w)) * 0.85 * Math.sin(Math.PI * e);
    }
    return { lon: wrapLon(a.lon + dlon * e), lat: lerp(a.lat, b.lat, e), w: Math.exp(lw) };
  }

  // Where the camera looks so a card's spot sits where new cards pop (CARD.y).
  function onCard(spot, close) {
    return { lon: spot[0], lat: spot[1] - ((H / 2 - CARD.y) * close) / W, w: close };
  }

  function cameraPath(data, m) {
    const views = m.scenes || [];
    const first = views.find(Boolean);
    const world = () => {
      const lit = views.filter(Boolean);
      if (!lit.length) return { lon: 10, lat: 25, w: 150 };
      const lon = lit.reduce((a, v) => a + v.fit[0], 0) / lit.length;
      const lat = lit.reduce((a, v) => a + v.fit[1], 0) / lit.length;
      return { lon, lat: clamp(lat, -30, 50), w: 150 };
    };
    const keys = [];
    keys.push({ t: 0, dur: 0, to: first ? { lon: first.fit[0], lat: first.fit[1], w: Math.min(110, first.wide * 1.5) } : world() });
    data.scenes.forEach((sc, i) => {
      if (sc.kind === "cta") {
        keys.push({ t: sc.start, dur: 1.1, to: world(), fly: true });
        return;
      }
      const v = views[i];
      if (!v) return;
      const [lon, lat, fit] = v.fit;
      const beats = sc.beats || [];
      const arrive = Math.max(0, sc.start - 0.05);
      keys.push({ t: arrive, dur: 0.8, to: { lon, lat, w: v.wide }, fly: true });
      const cards = beats.slice(1);
      let last = arrive + 0.35;
      cards.forEach((b, k) => {
        if (!v.spots[k]) return;
        const before = k ? cards[k - 1] : beats[0] || sc.start;
        const dur = clamp((b - before) * 0.75, 0.32, 0.55);
        const at = Math.max(b - dur * 0.8, last);
        keys.push({ t: at, dur, to: onCard(v.spots[k], v.close) });
        last = at + 0.2;
      });
      // All of it once more, every card small on it, before flying on.
      const end = cards.length ? cards[cards.length - 1] : beats[0] || sc.start;
      const back = Math.max(end + 0.85, sc.end - 0.75, last);
      // Close enough that its cards still read, as the reference pulls back (the whole
      // country is all but in view).
      if (back < sc.end - 0.15) keys.push({ t: back, dur: Math.min(0.6, sc.end - back), to: { lon, lat, w: fit * 0.86 } });
    });
    keys.sort((a, b) => a.t - b.t);
    // Each move starts from wherever the camera is when it starts (mid-move or drifting).
    const at = (k, t) => {
      const p = k.dur > 0 ? (t - k.t) / k.dur : 1;
      const cam = p < 1 ? between(k.from, k.to, p, k.fly) : { ...k.to };
      const settled = Math.max(0, t - k.t - k.dur);
      cam.w *= 1 - 0.035 * clamp(settled / 4); // a slow push in while it holds
      return cam;
    };
    keys[0].from = keys[0].to;
    for (let i = 1; i < keys.length; i++) keys[i].from = at(keys[i - 1], keys[i].t);
    return (t) => {
      let k = keys[0];
      for (const key of keys) {
        if (key.t <= t) k = key;
        else break;
      }
      return at(k, t);
    };
  }

  // Tiles of the Earth's picture: level 0 (the whole Earth, 18 tiles) is kept loaded under
  // everything; the level a frame's zoom needs is loaded as frames ask for it.
  function tileStore(base, m) {
    const cache = new Map();
    const MAX = 96;
    let clock = 0;
    const size = m.tile || 500;
    const levels = m.levels || 4;
    const get = (l, x, y) => {
      const key = `${l}/${x}_${y}`;
      let e = cache.get(key);
      if (!e) {
        const img = new Image();
        e = { img, ok: false, level: l };
        e.promise = new Promise((resolve) => {
          img.onload = () => {
            const done = () => {
              e.ok = true;
              resolve();
            };
            img.decode ? img.decode().then(done, done) : done();
          };
          img.onerror = () => resolve();
        });
        img.src = `${base}${l}/${x}_${y}.jpg`;
        cache.set(key, e);
        if (cache.size > MAX) {
          const old = [...cache.entries()].filter(([, v]) => v.level > 0 && v.ok).sort((a, b) => a[1].used - b[1].used);
          for (const [k] of old.slice(0, cache.size - MAX)) cache.delete(k);
        }
      }
      e.used = ++clock;
      return e;
    };
    const perDegree = (l) => (3000 * Math.pow(2, l)) / 360;
    const cols = (l) => 6 * Math.pow(2, l);
    const rows = (l) => 3 * Math.pow(2, l);
    const base0 = [];
    for (let y = 0; y < rows(0); y++) for (let x = 0; x < cols(0); x++) base0.push(get(0, x, y).promise);
    return { get, perDegree, cols, rows, size, levels, ready: Promise.all(base0) };
  }

  function drawTiles(ctx, store, cam, pr, level, wait) {
    const ppd = store.perDegree(level);
    const span = store.size / ppd; // degrees a tile covers
    const halfLon = W / 2 / (pr.s * pr.k);
    const halfLat = H / 2 / pr.s;
    const lon0 = cam.lon - halfLon;
    const lon1 = cam.lon + halfLon;
    const lat0 = Math.max(-90, cam.lat - halfLat);
    const lat1 = Math.min(90, cam.lat + halfLat);
    const x0 = Math.floor((lon0 + 180) / span);
    const x1 = Math.floor((lon1 + 180) / span);
    const y0 = Math.max(0, Math.floor((90 - lat1) / span));
    const y1 = Math.min(store.rows(level) - 1, Math.floor((90 - lat0) / span));
    const n = store.cols(level);
    for (let y = y0; y <= y1; y++) {
      for (let x = x0; x <= x1; x++) {
        const e = store.get(level, ((x % n) + n) % n, y);
        if (!e.ok) {
          if (wait) wait.push(e.promise);
          continue;
        }
        const tileLon = -180 + x * span;
        const sx = W / 2 + (tileLon - cam.lon) * pr.k * pr.s;
        const sy = H / 2 - (90 - y * span - cam.lat) * pr.s;
        const w = span * pr.k * pr.s;
        const h = span * pr.s;
        ctx.drawImage(e.img, sx - 0.5, sy - 0.5, w + 1, h + 1);
      }
    }
  }

  function ringPath(ctx, flat, cam, pr) {
    // The whole ring moved by whole turns to the camera's side (rings never cross 180).
    const shift = 360 * Math.round((cam.lon - flat[0]) / 360);
    ctx.moveTo(W / 2 + (flat[0] + shift - cam.lon) * pr.k * pr.s, H / 2 - (flat[1] - cam.lat) * pr.s);
    for (let i = 2; i < flat.length; i += 2) {
      ctx.lineTo(W / 2 + (flat[i] + shift - cam.lon) * pr.k * pr.s, H / 2 - (flat[i + 1] - cam.lat) * pr.s);
    }
    ctx.closePath();
  }

  function mapLayer(stage, data, options) {
    const canvas = el("canvas", "layer", stage);
    canvas.width = W;
    canvas.height = H;
    const ctx = canvas.getContext("2d");
    const m = data.map || { scenes: [], countries: {} };
    // The studio's address for tiles names their version (the renderer's is their folder).
    const base = /animation-map\/$/.test(options.mapBase || "") ? `${options.mapBase}v${m.version || 1}/` : options.mapBase;
    const store = m.tiles && base ? tileStore(base, m) : null;
    const camera = cameraPath(data, m);
    const horror = mapLookOf(data) === "horror";
    const views = m.scenes || [];
    const cta = data.scenes.find((sc) => sc.kind === "cta");
    // How lit each scene's country is at t: as it is named, until the camera flies on.
    const lit = data.scenes.map((sc, i) => {
      const v = views[i];
      if (!v) return null;
      const on = (sc.beats && sc.beats[0]) || sc.start;
      return (t) => {
        const end = ease.inOut(prog(t, sc.end + 0.1, 0.45));
        const finale = cta ? ease.out(prog(t, cta.start + 0.35, 0.6)) : 0;
        return Math.max(ease.out(prog(t, on - 0.1, 0.35)) * (1 - end), finale);
      };
    });
    const draw = (t) => {
      const cam = camera(t);
      const pr = project(cam);
      const wait = [];
      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = "source-over";
      ctx.shadowBlur = 0;
      ctx.fillStyle = OCEAN;
      ctx.fillRect(0, 0, W, H);
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = "high";
      if (store) {
        drawTiles(ctx, store, cam, pr, 0, null);
        let level = 0;
        while (level < store.levels - 1 && store.perDegree(level) < pr.s * 0.8) level++;
        if (level > 0) drawTiles(ctx, store, cam, pr, level, wait);
      }
      // The lit countries: a see-through colour, a bright edge with a glow, the name
      // spread wide and faint across it.
      views.forEach((v, i) => {
        if (!v || !lit[i]) return;
        const a = lit[i](t);
        const shape = m.countries[v.code];
        if (a <= 0.001 || !shape) return;
        ctx.beginPath();
        for (const ring of shape.rings) ringPath(ctx, ring, cam, pr);
        ctx.globalAlpha = a * (horror ? 0.86 : m.fillAlpha || 0.74);
        ctx.fillStyle = horror ? BLOOD : v.color;
        ctx.fill("evenodd");
        ctx.globalAlpha = a;
        ctx.lineJoin = "round";
        ctx.strokeStyle = horror ? "rgba(255,214,170,0.7)" : "rgba(255,255,255,0.95)";
        ctx.shadowColor = horror ? "rgba(255,60,40,0.7)" : "rgba(255,255,255,0.9)";
        ctx.shadowBlur = 9;
        ctx.lineWidth = 2.6;
        ctx.stroke();
        ctx.shadowBlur = 0;
        const size = clamp(0.17 * v.fit[2] * pr.s, 40, 900);
        ctx.font = `${size.toFixed(0)}px "Poppins-SemiBold", sans-serif`;
        if ("letterSpacing" in ctx) ctx.letterSpacing = `${(size * 0.42).toFixed(0)}px`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.globalAlpha = a * 0.14;
        ctx.fillStyle = "#ffffff";
        ctx.fillText(shape.short || shape.name || "", pr.x(shape.label[0]), pr.y(shape.label[1]));
        if ("letterSpacing" in ctx) ctx.letterSpacing = "0px";
      });
      ctx.globalAlpha = 1;
      if (horror) {
        // Everything darker and redder, the edges darkest, as the haunted reels look.
        ctx.fillStyle = "rgba(48,0,0,0.26)";
        ctx.fillRect(0, 0, W, H);
        const g = ctx.createRadialGradient(W / 2, H * 0.44, H * 0.16, W / 2, H * 0.5, H * 0.78);
        g.addColorStop(0, "rgba(0,0,0,0)");
        g.addColorStop(1, "rgba(0,0,0,0.74)");
        ctx.fillStyle = g;
        ctx.fillRect(0, 0, W, H);
      }
      return wait;
    };
    return { draw, camera, ready: store ? store.ready : Promise.resolve() };
  }

  // A thing's card pinned on its country: the painted picture (or its emoji) on white, its
  // name on a dark label below; it grows with the camera's zoom like the map under it.
  function mapCard(item, root) {
    const look = building ? building.look : "cards";
    const d = LOOK_CARDS[look];
    const wrap = place(el("div", "abs", root), 0, 0, d.w, d.h + d.gap + d.label);
    wrap.style.transformOrigin = `${d.w / 2}px ${d.h / 2}px`;
    wrap._dims = d;
    const card = place(el("div", "abs", wrap), 0, 0, d.w, d.h);
    if (look === "neon") {
      // No card: the dish itself on the map, a soft shadow under it.
      card.style.filter = "drop-shadow(0 18px 16px rgba(0,0,0,0.5))";
    } else if (look === "horror") {
      card.style.background = "radial-gradient(ellipse at 50% 40%, #3b1010, #0b0505)";
      card.style.border = `3px solid ${GOLD_EDGE}`;
      card.style.borderRadius = "6px";
      card.style.overflow = "hidden";
      card.style.boxShadow = "0 0 0 2px rgba(0,0,0,0.55), 0 16px 36px rgba(0,0,0,0.65), 0 0 22px rgba(217,164,65,0.35)";
    } else {
      card.style.background = "#ffffff";
      card.style.borderRadius = "18px";
      card.style.overflow = "hidden";
      card.style.boxShadow = "0 14px 34px rgba(0,0,0,0.42)";
    }
    const base = building && building.options.imageBase;
    if (item.image && base) {
      const img = place(el("img", "abs", card), 0, 0, d.w, d.h);
      img.style.objectFit = look === "neon" ? "contain" : "cover";
      img.alt = "";
      building.pending.push(
        new Promise((resolve) => {
          img.onload = img.onerror = () => resolve();
          img.src = base + item.image;
        }).then(() => img.decode && img.decode().catch(() => undefined)),
      );
    } else {
      const glyph = emoji(card, item.emoji || "🌍", { neon: 270, horror: 170 }[look] || 250);
      place(glyph, 0, 0, d.w, d.h);
      glyph.style.display = "flex";
      glyph.style.alignItems = "center";
      glyph.style.justifyContent = "center";
    }
    const holder = place(el("div", "abs", wrap), -200, d.h + d.gap, d.w + 400, d.label);
    holder.style.textAlign = "center";
    // A name written all in small letters gets capitals, as a label would.
    const name = String(item.text || "");
    const shown = name === name.toLowerCase() ? name.replace(/(^|\s)(\p{L})/gu, (m, a, b) => a + b.toUpperCase()) : name;
    const label = el("span", "", holder, look === "cards" ? shown : shown.toUpperCase());
    label.style.display = "inline-block";
    label.style.whiteSpace = "nowrap";
    label.style.lineHeight = "1.3";
    let size = 34;
    if (look === "horror") {
      size = 46;
      font(label, "Anton-Regular", size, GOLD);
      label.style.background = "rgba(10,7,5,0.92)";
      label.style.border = `2px solid ${GOLD_EDGE}`;
      label.style.borderRadius = "6px";
      label.style.padding = "1px 18px 3px";
      label.style.letterSpacing = "0.03em";
      label.style.textShadow = "0 0 8px rgba(246,196,83,0.85), 0 0 20px rgba(246,196,83,0.4)";
      label.style.boxShadow = "0 6px 16px rgba(0,0,0,0.5)";
    } else if (look === "neon") {
      // Each name its own neon colour, in turn through the reel.
      const c = NEON[building.neon++ % NEON.length];
      size = 54;
      font(label, "Anton-Regular", size, c);
      label.style.background = "rgba(18,16,24,0.9)";
      label.style.border = `2px solid ${c}55`;
      label.style.borderRadius = "10px";
      label.style.padding = "2px 18px 4px";
      label.style.letterSpacing = "0.03em";
      label.style.textShadow = `0 0 6px ${c}, 0 0 18px ${c}99`;
      label.style.boxShadow = "0 6px 16px rgba(0,0,0,0.45)";
    } else {
      font(label, "Poppins-SemiBold", size, "#ffffff");
      label.style.background = "#16141d";
      label.style.borderRadius = "10px";
      label.style.padding = "3px 18px 5px";
      label.style.boxShadow = "0 6px 16px rgba(0,0,0,0.35)";
    }
    for (; size > 18 && label.offsetWidth > d.w + 220; size -= 2) label.style.fontSize = size + "px";
    wrap._labelW = label.offsetWidth;
    wrap.style.display = "none";
    return wrap;
  }

  // A country's number (Visual.value: "#3" for a place in a ranking, "5,580" for a count),
  // with its icon (Visual.emoji): a dark badge, the number big and yellow; a count counts
  // up as it pops.
  function mapBadge(v, root) {
    const look = building ? building.look : "cards";
    const wrap = place(el("div", "abs", root), 0, 0);
    wrap.style.display = "flex";
    wrap.style.alignItems = "center";
    wrap.style.gap = "12px";
    wrap.style.padding = "6px 24px 6px 14px";
    wrap.style.whiteSpace = "nowrap";
    wrap.style.borderRadius = "16px";
    wrap.style.background = "rgba(16,16,20,0.92)";
    wrap.style.border = `2px solid ${look === "horror" ? GOLD_EDGE : "rgba(255,255,255,0.22)"}`;
    wrap.style.boxShadow = "0 10px 26px rgba(0,0,0,0.5)";
    if (v.emoji && v.emoji !== "🌍") font(el("span", "", wrap, v.emoji), "NotoColorEmoji", 64);
    const number = el("span", "", wrap, v.value);
    font(number, "Anton-Regular", 96, look === "horror" ? GOLD : "#FFD23F");
    number.style.lineHeight = "1.05";
    number.style.textShadow = "0 0 10px rgba(255,210,63,0.7), 0 0 26px rgba(255,210,63,0.35)";
    wrap._number = number;
    wrap._count = /^#/.test(v.value) ? null : parseValue(v.value);
    wrap.style.display = "flex";
    wrap._w = wrap.offsetWidth;
    wrap._h = wrap.offsetHeight;
    wrap.style.display = "none";
    return wrap;
  }

  function countTo(value, p) {
    const n = value.num * p;
    let text = n.toFixed(value.decimals);
    if (value.commas) text = Number(text).toLocaleString("en-US", { minimumFractionDigits: value.decimals, maximumFractionDigits: value.decimals });
    return value.head + text + value.tail;
  }

  function mapCountry(v, theme, root, scene, data) {
    const index = data.scenes.indexOf(scene);
    const view = data.map && data.map.scenes ? data.map.scenes[index] : null;
    const items = v.items || [];
    const cards = items.map((item) => mapCard(item, root));
    const beats = beatsOf(scene, items.length + 1).slice(1);
    const badge = v.value ? mapBadge(v, root) : null;
    const named = (scene.beats && scene.beats[0]) || scene.start;
    // Where each card (and the badge) is at t, or null while it isn't shown: a pure
    // function of t, so the captions can keep out of the cards' way at any moment.
    const layout = (t) => {
      const on = t >= scene.start - 0.001 && t < scene.end + 0.6;
      const cam = mapState.camera(t);
      const pr = project(cam);
      const zoom = view ? view.close / cam.w : 1;
      const leave = 1 - ease.inOut(prog(t, scene.end + 0.05, 0.4));
      const placed = cards.map((wrap, k) => {
        const at = beats[k];
        const next = beats[k + 1];
        // Off the map (no spot): one card at a time where cards pop.
        const gone = !view && next !== undefined && t >= next;
        if (!on || t < at - 0.02 || gone || leave <= 0.001) return null;
        const p = prog(t, at - 0.02, 0.4);
        const spot = view && view.spots[k];
        return {
          x: spot ? pr.x(spot[0]) : W / 2,
          y: spot ? pr.y(spot[1]) : CARD.y,
          s: zoom * lerp(0.2, 1, ease.back(p)),
          o: clamp(p * 3) * leave,
        };
      });
      let pin = null;
      if (badge && on && t >= named - 0.02 && leave > 0.001) {
        const p = prog(t, named - 0.02, 0.45);
        // Over the first card (the camera keeps it in view), else on the country itself;
        // never past the frame's edges.
        const spot = view && cards.length && view.spots[0];
        const d = cards.length ? cards[0]._dims : LOOK_CARDS.cards;
        const k = spot ? zoom : 1;
        let cx = W / 2;
        let bottom = CARD.y - d.h / 2 - 18;
        if (spot) {
          cx = pr.x(spot[0]);
          bottom = pr.y(spot[1]) - (d.h / 2 + 18) * zoom;
        } else if (view) {
          cx = pr.x(view.fit[0]);
          bottom = pr.y(view.fit[1]) + badge._h / 2;
        }
        const w = badge._w * k;
        pin = {
          x: clamp(cx - w / 2, 16, Math.max(16, W - 16 - w)),
          y: bottom - badge._h * k,
          k,
          s: k * lerp(0.3, 1, ease.back(p)),
          o: clamp(p * 3) * leave,
        };
      }
      return { placed, pin };
    };
    // The screen boxes the cards (with their labels) and the badge take at t.
    mapState.boxes.push((t) => {
      const { placed, pin } = layout(t);
      const out = [];
      placed.forEach((c, k) => {
        if (!c || c.o < 0.05) return;
        const d = cards[k]._dims;
        const half = (Math.max(d.w, cards[k]._labelW || d.w) / 2) * c.s;
        out.push({ x0: c.x - half, x1: c.x + half, y0: c.y - (d.h / 2) * c.s, y1: c.y + (d.h / 2 + d.gap + d.label) * c.s });
      });
      if (pin && pin.o >= 0.05) out.push({ x0: pin.x, x1: pin.x + badge._w * pin.s, y0: pin.y, y1: pin.y + badge._h * pin.s });
      return out;
    });
    return (t) => {
      const { placed, pin } = layout(t);
      cards.forEach((wrap, k) => {
        const c = placed[k];
        if (!c) {
          wrap.style.display = "none";
          return;
        }
        const d = wrap._dims;
        wrap.style.display = "block";
        wrap.style.opacity = c.o.toFixed(4);
        wrap.style.transform = `translate(${(c.x - d.w / 2).toFixed(1)}px, ${(c.y - d.h / 2).toFixed(1)}px) scale(${c.s.toFixed(4)})`;
      });
      if (!badge) return;
      if (!pin) {
        badge.style.display = "none";
        return;
      }
      badge.style.display = "flex";
      if (badge._count) badge._number.textContent = countTo(badge._count, ease.out(prog(t, named, 0.9)));
      badge.style.transformOrigin = "50% 100%";
      badge.style.opacity = pin.o.toFixed(4);
      badge.style.transform = `translate(${pin.x.toFixed(1)}px, ${pin.y.toFixed(1)}px) scale(${pin.s.toFixed(4)})`;
    };
  }

  // Under any other visual on the map (a hook, a number, the ending): the map darkened so
  // the visual reads.
  function mapVeil(root, kind, look) {
    const veil = place(el("div", "abs", root), 0, 0, W, H);
    veil.style.background =
      look === "horror"
        ? "radial-gradient(ellipse at 50% 42%, rgba(30,0,0,0.55), rgba(8,0,0,0.78) 75%)"
        : kind === "title"
        ? "radial-gradient(ellipse at 50% 42%, rgba(0,10,18,0.55), rgba(0,10,18,0.25) 70%)"
        : "linear-gradient(rgba(0,12,20,0.5), rgba(0,12,20,0.62))";
    return veil;
  }

  SCENES.country = (v, theme, root, scene, data) =>
    theme.map ? mapCountry(v, theme, root, scene, data) : SCENES.list(v, theme, root, scene);

  // ---- subtitles and header -----------------------------------------------------------

  function subtitles(theme, stage, captions, data) {
    const layer = el("div", "layer", stage);
    // Bold kinetic text: the words are the picture already.
    if (theme.kinetic) return { chunks: [], update() {} };
    if (theme.map) return mapSubtitles(theme, layer, captions, data);
    const chunks = captions.map((c) => {
      const box = place(el("div", "abs fit center", layer), (W - SUBS.w) / 2 - 20, SUBS.y, SUBS.w, SUBS.h);
      font(box, theme.caption, 78, theme.text);
      box.style.flexWrap = "wrap";
      box.style.alignContent = "center";
      box.style.display = "none";
      box.style.lineHeight = "1.12";
      const line = el("div", "line", box);
      // Room between words for the marker (paper) and the bigger spoken word (glow).
      line.style.wordSpacing = theme.glow ? "0.16em" : "0.22em";
      if (theme.story) {
        // A white card behind the words: readable on any of the scene's colours.
        line.style.display = "inline-block";
        line.style.width = "auto";
        line.style.background = "#ffffff";
        line.style.borderRadius = "28px";
        line.style.padding = "10px 30px 14px";
        line.style.boxShadow = "0 10px 0 rgba(0,0,0,0.12)";
        line.style.wordSpacing = "0.14em";
      }
      const spans = c.words.map((w, i) => {
        if (i) line.appendChild(document.createTextNode(" "));
        const s = el("span", "word", line, theme.glow ? w.text.toUpperCase() : w.text);
        if (theme.glow) {
          s.style.textShadow = "0 5px 0 rgba(0,0,0,0.55), 0 0 22px rgba(0,0,0,0.9)";
        } else if (theme.story) {
          s.style.color = theme.text;
        } else {
          s.style.textShadow = `3px 3px 0 ${theme.card}, -3px -3px 0 ${theme.card}, 3px -3px 0 ${theme.card}, -3px 3px 0 ${theme.card}`;
          // The marker is drawn around the word (a shadow), so it never takes the spaces.
          s.style.borderRadius = "8px";
        }
        return s;
      });
      box._fit = [78, 44];
      return { c, box, spans };
    });
    return {
      chunks,
      update(t) {
        for (const ch of chunks) {
          const on = t >= ch.c.start && t < ch.c.end;
          ch.box.style.display = on ? "flex" : "none";
          if (!on) continue;
          const p = ease.back(prog(t, ch.c.start, 0.18));
          show(ch.box, clamp(p * 1.5), `scale(${lerp(0.9, 1, p)})`);
          ch.spans.forEach((s, i) => {
            const w = ch.c.words[i];
            const next = ch.c.words[i + 1];
            const active = t >= w.start && t < (next ? next.start : ch.c.end);
            if (theme.glow) {
              s.style.color = active ? theme.accent3 : theme.text;
              s.style.transform = active ? "scale(1.08)" : "scale(1)";
            } else if (theme.story) {
              s.style.color = active ? theme.accent : theme.text;
            } else {
              s.style.background = active ? theme.accent3 : "transparent";
              s.style.boxShadow = active ? `0 0 0 6px ${theme.accent3}` : "none";
              s.style.transform = active ? "rotate(-1.5deg)" : "none";
            }
          });
        }
      },
    };
  }

  // World map captions: one word (or one name) at a time, low on the screen, white with a
  // soft shadow, the first letter a capital as the reference reels show them ("Has",
  // "Marine biologist"), punctuation off.
  function mapSubtitles(theme, layer, captions, data) {
    const bare = (text) => text.replace(/^[^\p{L}\p{N}$£€]+|[^\p{L}\p{N}%)]+$/gu, "");
    const shown = (text) => text.replace(/^\p{Ll}/u, (c) => c.toUpperCase());
    // The horror look: captions in capitals, the things' names and numbers in gold.
    const horror = mapLookOf(data) === "horror";
    const names = new Set();
    (data && data.scenes ? data.scenes : []).forEach((sc) => {
      if (sc.kind === "country" && sc.visual) (sc.visual.items || []).forEach((i) => names.add(plain(i.text)));
    });
    const chunks = captions.map((c) => {
      const box = place(el("div", "abs fit center", layer), 80, 1305, W - 160, 120);
      font(box, theme.caption, 60, theme.text);
      box.style.display = "none";
      box.style.whiteSpace = "nowrap";
      box.style.textShadow = "0 3px 10px rgba(0,0,0,0.65), 0 1px 2px rgba(0,0,0,0.6)";
      const said = c.words.map((w) => bare(w.text)).filter(Boolean).join(" ");
      box.textContent = horror ? said.toUpperCase() : shown(said);
      if (horror && (names.has(plain(said)) || /\d/.test(said))) {
        box.style.color = GOLD;
        box.style.textShadow = "0 0 10px rgba(246,196,83,0.55), 0 3px 10px rgba(0,0,0,0.7)";
      }
      box._fit = [60, 34];
      // Clear of the cards: the first of these bands no card, label or badge takes while
      // the words are shown (the reference keeps captions off the cards).
      const boxes = mapState && mapState.boxes ? mapState.boxes : [];
      const seen = [c.start, (c.start + c.end) / 2, Math.max(c.start, c.end - 0.02)].flatMap((t) => boxes.flatMap((f) => f(t)));
      const takes = (top) =>
        seen.reduce((sum, b) => {
          const w = Math.min(b.x1, W - 80) - Math.max(b.x0, 80);
          const h = Math.min(b.y1, top + 128) - Math.max(b.y0, top - 8);
          return sum + (w > 0 && h > 0 ? w * h : 0);
        }, 0);
      const bands = CAPTION_BANDS.map((top) => ({ top, hit: takes(top) }));
      const free = bands.find((b) => b.hit === 0) || bands.reduce((a, b) => (b.hit < a.hit ? b : a));
      box.style.top = free.top + "px";
      return { c, box, spans: [] };
    });
    return {
      chunks,
      update(t) {
        for (const ch of chunks) {
          const on = t >= ch.c.start && t < ch.c.end && ch.box.textContent;
          ch.box.style.display = on ? "flex" : "none";
          if (!on) continue;
          const p = ease.out(prog(t, ch.c.start, 0.12));
          show(ch.box, clamp(p * 1.4), `scale(${lerp(0.94, 1, p).toFixed(4)})`);
        }
      },
    };
  }

  function header(theme, stage, data) {
    if (theme.kinetic || theme.map) return { fits: [], update() {} };
    if (theme.stick) {
      // A big title on top for each part: the scene's own (who or where), else the reel's.
      const titles = new Map();
      const boxFor = (text) => {
        if (!titles.has(text)) {
          const box = place(el("div", "abs fit center", stage), 90, SAFE.top + 6, 900, 104);
          font(box, theme.display, 84, theme.ink);
          box.textContent = text.toUpperCase();
          box.style.letterSpacing = "0.02em";
          box._fit = [84, 40];
          titles.set(text, box);
        }
        return titles.get(text);
      };
      const per = data.scenes.map((sc) => boxFor((sc.kind === "scene" && sc.visual && sc.visual.text) || data.title || ""));
      return {
        fits: [...titles.values()],
        update(t) {
          let i = data.scenes.findIndex((sc) => t >= sc.start && t < sc.end);
          if (i < 0) i = data.scenes.length - 1;
          for (const box of titles.values()) box.style.display = box === per[i] ? "flex" : "none";
        },
      };
    }
    const wrap = place(el("div", "abs", stage), 170, SAFE.top + 10, 740, 96);
    const pill = place(el("div", "abs fit center", wrap), 0, 0, 740, 72);
    font(pill, theme.body, 38, theme.text);
    pill.textContent = data.title || "";
    pill.style.borderRadius = "40px";
    pill.style.padding = "0 34px";
    pill.style.whiteSpace = "nowrap";
    pill.style.overflow = "hidden";
    if (theme.glow) {
      pill.style.background = "rgba(10,14,40,0.72)";
      pill.style.border = `2px solid ${theme.accent}88`;
    } else {
      pill.style.background = theme.card;
      pill.style.border = `4px solid ${theme.ink}`;
    }
    pill._fit = [38, 24];
    const track = place(el("div", "abs", wrap), 60, 86, 620, 8);
    track.style.borderRadius = "4px";
    track.style.background = theme.glow ? "rgba(255,255,255,0.14)" : "rgba(0,0,0,0.12)";
    const bar = place(el("div", "abs", track), 0, 0, 620, 8);
    bar.style.borderRadius = "4px";
    bar.style.background = theme.glow ? `linear-gradient(90deg, ${theme.accent}, ${theme.accent2})` : theme.accent;
    bar.style.transformOrigin = "0 50%";
    const start = data.header ? data.header.start : 2;
    return {
      fits: [pill],
      update(t) {
        show(wrap, ease.out(prog(t, start, 0.5)), `translateY(${(1 - ease.out(prog(t, start, 0.5))) * -20}px)`);
        show(bar, 1, `scaleX(${clamp(t / Math.max(0.1, data.duration)).toFixed(4)})`);
      },
    };
  }

  // ---- painted pictures -----------------------------------------------------------------

  // The scene's painting, cover-fitted, with a veil that keeps each style's text readable;
  // its loading is added to `pending`. None for styles drawn as themselves.
  function paintedLayer(scene, theme, root, base, pending) {
    const name = scene.visual && scene.visual.image;
    if (!name || !base || theme.stick || theme.kinetic || theme.map) return null;
    const box = el("div", "abs", root);
    place(box, 0, 0, W, H);
    box.style.overflow = "hidden";
    const img = el("img", "abs", box);
    place(img, 0, 0, W, H);
    img.style.objectFit = "cover";
    img.style.transformOrigin = "50% 45%";
    if (theme.paper) img.style.filter = "sepia(0.35) saturate(0.8)";
    img.alt = "";
    pending.push(
      new Promise((resolve) => {
        img.onload = img.onerror = () => resolve();
        img.src = base + name;
      }).then(() => img.decode && img.decode().catch(() => undefined)),
    );
    const veil = el("div", "abs", box);
    place(veil, 0, 0, W, H);
    const acted = scene.kind === "scene";
    veil.style.background = theme.glow
      ? "linear-gradient(rgba(5,6,15,0.45), rgba(5,6,15,0.62) 55%, rgba(5,6,15,0.85))"
      : theme.story
        ? acted
          ? "linear-gradient(rgba(0,0,0,0) 70%, rgba(0,0,0,0.18))"
          : "rgba(255,244,230,0.38)"
        : "rgba(245,239,227,0.5)";
    return img;
  }

  // ---- the player ---------------------------------------------------------------------

  let state = null;

  async function loadFonts(base, emojiUrl) {
    if (!base) return;
    const faces = FONTS.map((name) => new FontFace(name, `url("${base}${name}.ttf")`));
    if (emojiUrl) faces.push(new FontFace("NotoColorEmoji", `url("${emojiUrl}")`));
    await Promise.all(
      faces.map((f) =>
        f.load().then(
          (loaded) => document.fonts.add(loaded),
          () => null, // a missing font falls back; the emoji font is optional
        ),
      ),
    );
    await document.fonts.ready;
  }

  async function load(data, options = {}) {
    await loadFonts(options.fontBase, options.emojiFont);
    const stage = document.getElementById("stage");
    stage.textContent = "";
    shrunk.length = 0;
    const theme = THEMES[data.style] || THEMES.glow;
    const seed = hash(data.title || "reel");
    const mapView = theme.map ? mapLayer(stage, data, options) : null;
    if (mapView) mapView.boxes = [];
    mapState = mapView;
    const drawBg = mapView ? mapView.draw : background(theme, stage, seed);
    const sceneLayer = el("div", "layer", stage);
    const pending = mapView ? [mapView.ready] : [];
    building = { pending, options, look: mapLookOf(data), neon: 0 };
    const scenes = data.scenes.map((scene) => {
      const root = el("div", "scene", sceneLayer);
      const kind = theme.kinetic && !["cta", "stat"].includes(scene.kind) ? "kinetic" : scene.kind;
      const build = SCENES[kind] || SCENES.title;
      root.style.display = "block";
      if (theme.map && kind !== "country") mapVeil(root, kind, building.look);
      // A painted picture behind it (animate/images.py), under everything the scene draws.
      const picture = paintedLayer(scene, theme, root, options.imageBase, pending);
      scene._picture = Boolean(picture);
      const update = build(scene.visual || {}, theme, root, scene, data);
      return { scene, root, update, picture, pinned: theme.map && kind === "country" };
    });
    building = null;
    // Every picture is in before a frame is drawn: the first frames of a render too.
    await Promise.all(pending);
    const subs = subtitles(theme, stage, data.captions || [], data);
    const head = header(theme, stage, data);
    const guides = [
      [0, 0, W, SAFE.top],
      [0, H - SAFE.bottom, W, SAFE.bottom],
      [W - SAFE.right, 0, SAFE.right, H],
    ].map(([x, y, w, h]) => place(el("div", "safe-guide", stage), x, y, w, h));
    // Fit every text to its box while all of it is laid out, then hide what isn't on.
    const fitAll = (root) =>
      root.querySelectorAll(".fit").forEach((node) => {
        if (node._fit) fit(node, node._fit[0], node._fit[1]);
      });
    fitAll(sceneLayer);
    subs.chunks.forEach((c) => {
      c.box.style.display = "flex";
      fit(c.box, c.box._fit[0], c.box._fit[1], "subtitle: " + c.box.textContent.slice(0, 30));
      c.box.style.display = "none";
    });
    head.fits.forEach((node) => fit(node, node._fit[0], node._fit[1], "title"));
    scenes.forEach((s) => (s.root.style.display = "none"));
    state = { data, theme, drawBg, scenes, subs, head, guides, t: 0 };
    await seek(0);
    return { duration: data.duration, problems: shrunk.slice() };
  }

  // Draws the frame at t. When the frame needs map tiles still loading, it is drawn with
  // what is there and a promise is returned that draws it again once they are in (the
  // renderer waits for it; the preview just gets the sharper frame a moment later).
  function seek(t) {
    if (!state) return;
    state.t = t;
    const waiting = state.drawBg(t);
    const last = state.scenes[state.scenes.length - 1];
    for (const s of state.scenes) {
      const { start, end } = s.scene;
      if (s.pinned) {
        // A country's cards sit on the map: no slide of their own, on until the camera
        // has flown on.
        const pinnedOn = t >= start - 0.001 && t < end + 0.6;
        s.root.style.display = pinnedOn ? "block" : "none";
        if (!pinnedOn) continue;
        show(s.root, 1, "none");
        s.update(t);
        continue;
      }
      // The closing scene stays to the very end; the others hand over to the next.
      const stays = s === last;
      const on = t >= start - 0.001 && (stays || t < end + EXIT);
      s.root.style.display = on ? "block" : "none";
      if (!on) continue;
      const pin = ease.out(prog(t, start + 0.08, ENTER));
      const pout = stays ? 0 : ease.inOut(prog(t, end - 0.02, EXIT));
      const first = s === state.scenes[0];
      const enter = first ? 1 : pin;
      // A slow push-in keeps a long scene alive.
      const push = 0.025 * prog(t, start, Math.max(1, end - start));
      show(s.root, enter * (1 - pout), `translateY(${((1 - enter) * 50 - pout * 60).toFixed(1)}px) scale(${(lerp(0.97, 1, enter) + pout * 0.03 + push).toFixed(4)})`);
      if (s.picture) {
        // A slow push and drift across the painting while the scene lasts.
        const k = prog(t, start, Math.max(1, end - start + EXIT));
        s.picture.style.transform = `scale(${(1.04 + 0.07 * k).toFixed(4)}) translate(${(-14 * k).toFixed(1)}px, ${(-10 * k).toFixed(1)}px)`;
      }
      s.update(t);
    }
    state.subs.update(t);
    state.head.update(t);
    if (waiting && waiting.length) {
      return Promise.all(waiting).then(() => {
        if (state && state.t === t) state.drawBg(t);
      });
    }
    return undefined;
  }

  function guides(on) {
    if (state) state.guides.forEach((g) => (g.style.display = on ? "block" : "none"));
  }

  // The preview fits the 1080x1920 stage to its window.
  function scaleToWindow() {
    const frame = document.getElementById("frame");
    const s = Math.min(window.innerWidth / W, window.innerHeight / H);
    frame.style.transform = `translate(${(window.innerWidth - W * s) / 2}px, ${(window.innerHeight - H * s) / 2}px) scale(${s})`;
  }

  const preview = new URLSearchParams(location.search).get("mode") === "preview";
  if (preview) {
    scaleToWindow();
    window.addEventListener("resize", scaleToWindow);
    window.addEventListener("message", async (event) => {
      if (event.origin !== location.origin || !event.data || typeof event.data !== "object") return;
      const msg = event.data;
      if (msg.type === "load") {
        const info = await load(msg.data, {
          fontBase: msg.fontBase,
          emojiFont: msg.emojiFont,
          imageBase: msg.imageBase,
          mapBase: msg.mapBase,
        });
        seek(msg.t || 0);
        window.parent.postMessage({ type: "ready", duration: info.duration, problems: info.problems }, location.origin);
      } else if (msg.type === "seek") {
        seek(Number(msg.t) || 0);
      } else if (msg.type === "guides") {
        guides(Boolean(msg.on));
      }
    });
    window.parent.postMessage({ type: "player" }, location.origin);
  }

  window.Player = { load, seek, guides, problems: () => shrunk.slice(), SAFE, BOX, W, H };
})();
