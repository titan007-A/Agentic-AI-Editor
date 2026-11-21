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
    const spans = words(head, v.text || "");
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
    return (t) => {
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

  // ---- subtitles and header -----------------------------------------------------------

  function subtitles(theme, stage, captions) {
    const layer = el("div", "layer", stage);
    const chunks = captions.map((c) => {
      const box = place(el("div", "abs fit center", layer), (W - SUBS.w) / 2 - 20, SUBS.y, SUBS.w, SUBS.h);
      font(box, theme.caption, 78, theme.text);
      box.style.flexWrap = "wrap";
      box.style.alignContent = "center";
      box.style.display = "none";
      box.style.lineHeight = "1.12";
      const line = el("div", "line", box);
      const spans = c.words.map((w, i) => {
        if (i) line.appendChild(document.createTextNode(" "));
        const s = el("span", "word", line, theme.glow ? w.text.toUpperCase() : w.text);
        if (theme.glow) {
          s.style.textShadow = "0 5px 0 rgba(0,0,0,0.55), 0 0 22px rgba(0,0,0,0.9)";
        } else {
          s.style.webkitTextStroke = `0px`;
          s.style.textShadow = `3px 3px 0 ${theme.card}, -3px -3px 0 ${theme.card}, 3px -3px 0 ${theme.card}, -3px 3px 0 ${theme.card}`;
          s.style.padding = "0 8px";
          s.style.margin = "0 -5px";
          s.style.borderRadius = "14px";
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
            } else {
              s.style.background = active ? theme.accent3 : "transparent";
              s.style.transform = active ? "scale(1.06) rotate(-1deg)" : "scale(1)";
            }
          });
        }
      },
    };
  }

  function header(theme, stage, data) {
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
    const drawBg = background(theme, stage, seed);
    const sceneLayer = el("div", "layer", stage);
    const scenes = data.scenes.map((scene) => {
      const root = el("div", "scene", sceneLayer);
      const build = SCENES[scene.kind] || SCENES.title;
      root.style.display = "block";
      const update = build(scene.visual || {}, theme, root, scene);
      return { scene, root, update };
    });
    const subs = subtitles(theme, stage, data.captions || []);
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
    seek(0);
    return { duration: data.duration, problems: shrunk.slice() };
  }

  function seek(t) {
    if (!state) return;
    state.t = t;
    state.drawBg(t);
    const last = state.scenes[state.scenes.length - 1];
    for (const s of state.scenes) {
      const { start, end } = s.scene;
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
      s.update(t);
    }
    state.subs.update(t);
    state.head.update(t);
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
        const info = await load(msg.data, { fontBase: msg.fontBase, emojiFont: msg.emojiFont });
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
