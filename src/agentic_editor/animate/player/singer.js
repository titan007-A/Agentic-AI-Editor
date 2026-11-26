/* Singer edit (style "singer", animate/singer.py): a song's hook as a lyric edit.
 *
 * One scene a sung line: a warm, gritty backdrop (the painted picture, else drawn from the
 * singer's own photo), the singer cut out and standing in it in one of a few shots, and
 * every sung word slapped on as a torn-paper sticker the moment it is sung, the key words in
 * red handwriting, small side notes and red doodles around; whips, punches, flashes and
 * tears between lines, grain and dust over all of it.
 *
 * Like the rest of the player, every frame is a pure function of t: no clock, no CSS
 * animation, randomness only from seeded generators. player.js hands its helpers in
 * (`kit`) and calls load() for this style instead of its own scenes.
 */
(function () {
  "use strict";

  const FONTS = ["AbrilFatface-Regular", "Kalam-Bold", "Martel-Heavy", "PermanentMarker-Regular"];
  const SERIF = '"AbrilFatface-Regular", "Martel-Heavy", serif';
  const SERIF_HEAVY = '"Martel-Heavy", "AbrilFatface-Regular", serif';
  const SCRIPT = '"Kalam-Bold", "Martel-Heavy", cursive';
  const MARKER = '"PermanentMarker-Regular", "Kalam-Bold", sans-serif';
  const RED = "#c8141e";
  const PAPER = ["#f3eee3", "#ece4d2", "#f7f3ea", "#e9dfc9"];
  const INK = "#141210";
  const SMALL = new Set(
    "a an the of to in on is am are be i se ka ki ke ko hai hain ho hu hoon main mai me mein ne na ya to bhi jo wo ye tu tum".split(
      " ",
    ),
  );
  const BASE = { normal: 134, key: 172, small: 94 };
  const OVERLAP = 0.03; // of a sticker's height, how far the next one in a row tucks under
  const ROW = 0.94; // rows of stickers, this much of a row's height apart
  // Where the words may go, by shot (x0, y0, x1, y1), and how the block sits in it.
  const ZONES = {
    hero: [[70, 250, 900, 790], "left"],
    close: [[70, 250, 930, 820], "center"],
    far: [[70, 290, 930, 900], "center"],
    print: [[70, 250, 930, 800], "center"],
    mirror: [[70, 250, 930, 700], "left"],
    echo: [[70, 250, 930, 760], "center"],
    silhouette: [[70, 250, 930, 820], "center"],
    words: [[70, 330, 930, 1400], "middle"],
    painted: [[70, 250, 930, 860], "left"],
  };
  const TRANSITIONS = ["whip", "punch", "flash", "tear", "glitch", "punch", "whip", "flash"];
  const BACKDROPS = {
    hero: ["photo", "wall"],
    close: ["photo", "wall"],
    far: ["crowd", "sky"],
    print: ["wall"],
    mirror: ["wall"],
    echo: ["crowd"],
    silhouette: ["sky"],
    words: ["wall"],
    painted: ["sky", "wall"],
  };

  function install(kit) {
    const { el, place, rng, hash, ease, clamp, lerp, prog, W, H } = kit;
    const plain = (s) =>
      String(s || "")
        .toLowerCase()
        .replace(/[^\p{L}\p{N}]+/gu, "");

    async function loadFonts(base) {
      if (!base) return;
      await Promise.all(
        FONTS.map((name) =>
          new FontFace(name, `url("${base}${name}.ttf")`).load().then(
            (f) => document.fonts.add(f),
            () => null,
          ),
        ),
      );
      await document.fonts.ready;
    }

    function picture(src) {
      return new Promise((resolve) => {
        if (!src) return resolve(null);
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = () => resolve(null);
        img.src = src;
      });
    }

    function canvas(w, h) {
      const c = document.createElement("canvas");
      c.width = Math.max(1, Math.round(w));
      c.height = Math.max(1, Math.round(h));
      return c;
    }

    function cover(ctx, img, w, h, zoom = 1) {
      const s = Math.max(w / img.width, h / img.height) * zoom;
      const dw = img.width * s;
      const dh = img.height * s;
      ctx.drawImage(img, (w - dw) / 2, (h - dh) / 2, dw, dh);
    }

    // ---- backdrops (drawn once, at half size: they sit behind and are soft anyway) ------

    const BW = W / 2;
    const BH = H / 2;

    function grunge(ctx, r, amount) {
      for (let i = 0; i < amount; i++) {
        const x = r() * BW;
        const y = r() * BH;
        const w = 20 + r() * 140;
        const h = 8 + r() * 60;
        ctx.save();
        ctx.translate(x, y);
        ctx.rotate((r() - 0.5) * 0.5);
        const shade = Math.floor(40 + r() * 70);
        ctx.fillStyle = `rgba(${shade + 30},${shade + 15},${shade},${(0.05 + r() * 0.12).toFixed(3)})`;
        ctx.fillRect(-w / 2, -h / 2, w, h);
        if (r() < 0.35) {
          // a newspaper scrap: lines of "print"
          ctx.fillStyle = `rgba(210,200,180,${(0.08 + r() * 0.1).toFixed(3)})`;
          ctx.fillRect(-w / 2, -h / 2, w, h);
          ctx.fillStyle = "rgba(30,25,20,0.18)";
          for (let k = 4; k < h - 3; k += 5) ctx.fillRect(-w / 2 + 4, -h / 2 + k, w - 8 - r() * 20, 1.5);
        }
        ctx.restore();
      }
      ctx.strokeStyle = "rgba(0,0,0,0.35)";
      ctx.lineWidth = 1;
      for (let i = 0; i < 14; i++) {
        ctx.beginPath();
        let x = r() * BW;
        let y = r() * BH;
        ctx.moveTo(x, y);
        for (let k = 0; k < 6; k++) {
          x += (r() - 0.5) * 60;
          y += r() * 50;
          ctx.lineTo(x, y);
        }
        ctx.stroke();
      }
    }

    function beam(ctx, x, strength) {
      const g = ctx.createRadialGradient(x, -40, 10, x, BH * 0.35, BH * 0.8);
      g.addColorStop(0, `rgba(255,196,120,${0.75 * strength})`);
      g.addColorStop(0.35, `rgba(255,150,60,${0.28 * strength})`);
      g.addColorStop(1, "rgba(0,0,0,0)");
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, BW, BH);
    }

    function vignette(ctx, strength = 0.75) {
      const g = ctx.createRadialGradient(BW / 2, BH * 0.45, BH * 0.2, BW / 2, BH * 0.5, BH * 0.78);
      g.addColorStop(0, "rgba(0,0,0,0)");
      g.addColorStop(1, `rgba(8,4,2,${strength})`);
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, BW, BH);
    }

    function photoBackdrop(ctx, photo, r) {
      ctx.fillStyle = "#1b120b";
      ctx.fillRect(0, 0, BW, BH);
      if (photo) {
        ctx.save();
        ctx.filter = "blur(16px) sepia(0.55) saturate(1.3) brightness(0.55) contrast(1.15)";
        cover(ctx, photo, BW, BH, 1.25);
        ctx.restore();
      }
      const g = ctx.createLinearGradient(0, 0, 0, BH);
      g.addColorStop(0, "rgba(30,16,8,0.55)");
      g.addColorStop(0.5, "rgba(120,60,20,0.18)");
      g.addColorStop(1, "rgba(15,8,4,0.7)");
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, BW, BH);
      grunge(ctx, r, 40);
      beam(ctx, BW * (0.3 + r() * 0.4), 0.7);
      vignette(ctx);
    }

    function wallBackdrop(ctx, r) {
      ctx.fillStyle = "#1a110b";
      ctx.fillRect(0, 0, BW, BH);
      // bricks of an old wall, barely there
      for (let y = 0; y < BH; y += 26) {
        const off = (y / 26) % 2 ? 0 : 30;
        for (let x = -60 + off; x < BW; x += 60) {
          const s = Math.floor(28 + r() * 22);
          ctx.fillStyle = `rgb(${s + 14},${s + 6},${s})`;
          ctx.fillRect(x + 1, y + 1, 57, 23);
        }
      }
      grunge(ctx, r, 120);
      beam(ctx, BW * (0.25 + r() * 0.5), 1);
      vignette(ctx, 0.85);
    }

    function skyBackdrop(ctx, r) {
      const g = ctx.createLinearGradient(0, 0, 0, BH);
      g.addColorStop(0, "#1c1220");
      g.addColorStop(0.35, "#7a2f14");
      g.addColorStop(0.58, "#e0701e");
      g.addColorStop(0.7, "#ffc25a");
      g.addColorStop(0.72, "#2a160c");
      g.addColorStop(1, "#0d0705");
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, BW, BH);
      const sx = BW * (0.3 + r() * 0.4);
      const sy = BH * 0.64;
      const sun = ctx.createRadialGradient(sx, sy, 4, sx, sy, BW * 0.7);
      sun.addColorStop(0, "rgba(255,245,200,1)");
      sun.addColorStop(0.08, "rgba(255,210,120,0.9)");
      sun.addColorStop(0.35, "rgba(255,140,40,0.3)");
      sun.addColorStop(1, "rgba(255,120,30,0)");
      ctx.fillStyle = sun;
      ctx.fillRect(0, 0, BW, BH);
      // clouds: soft dark streaks
      for (let i = 0; i < 18; i++) {
        ctx.fillStyle = `rgba(40,18,12,${(0.15 + r() * 0.25).toFixed(3)})`;
        ctx.beginPath();
        ctx.ellipse(r() * BW, BH * (0.08 + r() * 0.45), 60 + r() * 120, 6 + r() * 14, 0, 0, Math.PI * 2);
        ctx.fill();
      }
      // a city against the light
      ctx.fillStyle = "#0b0605";
      let x = -10;
      while (x < BW) {
        const w = 14 + r() * 34;
        const h = BH * (0.05 + r() * 0.16);
        ctx.fillRect(x, BH * 0.72 - h, w, h + 4);
        if (r() < 0.5) {
          ctx.fillStyle = "rgba(255,190,90,0.55)";
          for (let k = 0; k < 6; k++) ctx.fillRect(x + 3 + r() * (w - 6), BH * 0.72 - h + 4 + r() * (h - 8), 2, 2);
          ctx.fillStyle = "#0b0605";
        }
        x += w + r() * 4;
      }
      vignette(ctx, 0.6);
    }

    function crowdBackdrop(ctx, r) {
      const g = ctx.createLinearGradient(0, 0, 0, BH);
      g.addColorStop(0, "#21160f");
      g.addColorStop(0.6, "#3a2414");
      g.addColorStop(1, "#120a06");
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, BW, BH);
      beam(ctx, BW / 2, 1.1);
      // rows of grey, faceless people, smaller as they go back
      for (let row = 0; row < 6; row++) {
        const size = 16 + row * 9;
        const y = BH * 0.42 + row * row * 6 + row * 26;
        const shade = Math.floor(60 + (5 - row) * 14);
        ctx.fillStyle = `rgba(${shade},${shade - 6},${shade - 12},${(0.55 + row * 0.07).toFixed(3)})`;
        for (let x = -size + r() * size; x < BW + size; x += size * (1.4 + r() * 0.5)) {
          ctx.beginPath();
          ctx.arc(x, y, size * 0.42, 0, Math.PI * 2);
          ctx.fill();
          ctx.beginPath();
          ctx.ellipse(x, y + size * 1.2, size * 0.75, size * 0.9, 0, Math.PI, 0);
          ctx.fill();
          ctx.fillRect(x - size * 0.75, y + size * 1.2, size * 1.5, BH);
        }
      }
      const fog = ctx.createLinearGradient(0, BH * 0.3, 0, BH);
      fog.addColorStop(0, "rgba(40,24,14,0)");
      fog.addColorStop(1, "rgba(18,10,6,0.65)");
      ctx.fillStyle = fog;
      ctx.fillRect(0, 0, BW, BH);
      vignette(ctx, 0.7);
    }

    function paintedBackdrop(ctx, img) {
      ctx.save();
      ctx.filter = "sepia(0.18) saturate(1.1) contrast(1.06)";
      cover(ctx, img, BW, BH);
      ctx.restore();
      vignette(ctx, 0.55);
    }

    // A torn frame over the scene (dark paper edges), now and then.
    function tornFrame(r) {
      const c = canvas(BW, BH);
      const ctx = c.getContext("2d");
      ctx.fillStyle = "#120c08";
      ctx.beginPath();
      ctx.rect(0, 0, BW, BH);
      const inset = (side) => 10 + r() * 26 + side;
      const pts = [];
      const steps = 26;
      for (let i = 0; i <= steps; i++) pts.push([(BW * i) / steps, inset(0)]);
      for (let i = 0; i <= steps * 1.7; i++) pts.push([BW - inset(0), (BH * i) / (steps * 1.7)]);
      for (let i = steps; i >= 0; i--) pts.push([(BW * i) / steps, BH - inset(6)]);
      for (let i = Math.floor(steps * 1.7); i >= 0; i--) pts.push([inset(0), (BH * i) / (steps * 1.7)]);
      ctx.moveTo(pts[0][0], pts[0][1]);
      pts.forEach(([x, y]) => ctx.lineTo(x, y));
      ctx.closePath();
      ctx.fill("evenodd");
      // a lighter paper lip along the tear
      ctx.strokeStyle = "rgba(220,205,180,0.5)";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(pts[0][0], pts[0][1]);
      pts.forEach(([x, y]) => ctx.lineTo(x + (r() - 0.5) * 2, y + (r() - 0.5) * 2));
      ctx.closePath();
      ctx.stroke();
      return c;
    }

    // ---- the singer, graded once per photo and look -------------------------------------

    const LOOKS = {
      warm: "sepia(0.2) saturate(1.06) contrast(1.07) brightness(0.98) drop-shadow(0 0 16px rgba(255,166,74,0.9)) drop-shadow(0 0 3px rgba(255,214,150,0.9))",
      faded: "grayscale(0.75) sepia(0.35) brightness(0.72) contrast(1.05)",
      dark: "brightness(0) drop-shadow(0 0 20px rgba(255,170,80,1)) drop-shadow(0 0 5px rgba(255,226,170,1))",
      mirror: "sepia(0.3) brightness(0.8) contrast(1.04) blur(0.6px)",
    };

    // The cutout in a look, made once per photo; each scene copies only the part of it that
    // is on screen (pictures read from disk can't be turned into new pictures, only drawn).
    async function graded(photo, look) {
      if (photo.looks[look]) return photo.looks[look];
      const img = photo.cutImg;
      const MAXH = 1500;
      const s = Math.min(1, MAXH / img.height);
      const pad = 40;
      const c = canvas(img.width * s + pad * 2, img.height * s + pad * 2);
      const ctx = c.getContext("2d");
      ctx.filter = LOOKS[look];
      ctx.drawImage(img, pad, pad, img.width * s, img.height * s);
      const made = { canvas: c, width: c.width, height: c.height, pad, scale: s };
      photo.looks[look] = made;
      return made;
    }

    // Where a cutout goes for a shot: its scale and top-left, from where its face should be.
    function placement(shot, photo, cut) {
      const [fx, fy, fw, fh] = photo.face && photo.face.length === 4 ? photo.face : [0.34, 0.02, 0.32, 0.2];
      const ch = cut.height;
      const cw = cut.width;
      const faceH = Math.max(0.04, fh) * ch;
      const below = (1 - fy - fh) / Math.max(0.04, fh); // face heights of body under the face
      const full = below >= 5.5;
      const want = {
        hero: [290, 600, 1090],
        close: [560, 540, 1260],
        far: [150, 540, 1240],
        mirror: [250, 330, 1100],
        echo: [215, 540, 1080],
        silhouette: [255, 560, 1060],
        painted: [230, 620, 1040],
        words: [230, 620, 1040],
        print: [230, 620, 1040],
      }[shot] || [230, 620, 1040];
      let s = want[0] / faceH;
      if (shot === "far" && full) s = (H * 0.5) / ch;
      const faceX = (fx + fw / 2) * cw;
      const faceY = (fy + fh / 2) * ch;
      let x = want[1] - faceX * s;
      let y = want[2] - faceY * s;
      if (shot === "far" && full) y = 1600 - ch * s;
      if (!full && y + ch * s < H + 8) {
        // a photo cut at the waist never shows its cut edge: it stands on the frame's bottom
        y = H + 8 - ch * s;
        const faceAt = y + faceY * s;
        if (faceAt > 1380) {
          const grow = (H + 8 - 1380) / (H + 8 - faceAt);
          s *= grow;
          x = want[1] - faceX * s;
          y = H + 8 - ch * s;
        }
      }
      return { s, x, y, face: [x + faceX * s, y + faceY * s, faceH * s], full };
    }

    // ---- torn paper stickers ------------------------------------------------------------

    function torn(r, rough = 1) {
      const pts = [];
      const jag = () => (r() * 7 * rough).toFixed(1);
      const n = 9;
      for (let i = 0; i <= n; i++) pts.push(`${((100 * i) / n).toFixed(1)}% ${jag()}%`);
      for (let i = 1; i < 4; i++) pts.push(`${(100 - r() * 3 * rough).toFixed(1)}% ${((100 * i) / 4).toFixed(1)}%`);
      for (let i = n; i >= 0; i--) pts.push(`${((100 * i) / n).toFixed(1)}% ${(100 - r() * 7 * rough).toFixed(1)}%`);
      for (let i = 3; i > 0; i--) pts.push(`${(r() * 3 * rough).toFixed(1)}% ${((100 * i) / 4).toFixed(1)}%`);
      return `polygon(${pts.join(", ")})`;
    }

    function styleOf(role, k, theme, r) {
      if (role === "key")
        return r() < 0.5
          ? { family: SCRIPT, color: RED, paper: PAPER[k % 4], weight: 700, upper: false, underline: true }
          : { family: MARKER, color: RED, paper: PAPER[(k + 1) % 4], upper: true, underline: r() < 0.5 };
      if (theme === "caps") return { family: MARKER, color: INK, paper: PAPER[k % 4], upper: true };
      const pick = (k + theme.length) % 4;
      if (pick === 0 || role === "small") return { family: SERIF, color: INK, paper: PAPER[k % 4], upper: false };
      if (pick === 1) return { family: SERIF_HEAVY, color: "#f4efe4", paper: "#141110", upper: false };
      if (pick === 2) return { family: SCRIPT, color: INK, paper: PAPER[(k + 2) % 4], upper: false };
      return { family: SERIF, color: INK, paper: PAPER[(k + 3) % 4], upper: true };
    }

    function sticker(parent, text, st, size, r) {
      const box = el("div", "abs", parent);
      box.style.filter = "drop-shadow(0 8px 10px rgba(0,0,0,0.55))";
      box.style.transformOrigin = "50% 50%";
      box.style.whiteSpace = "nowrap";
      const paper = el("div", "", box);
      paper.style.background = st.paper;
      paper.style.backgroundImage =
        st.paper === "#141110"
          ? "radial-gradient(circle at 30% 20%, rgba(255,255,255,0.08), transparent 60%)"
          : "radial-gradient(circle at 70% 30%, rgba(255,255,255,0.5), transparent 55%), radial-gradient(circle at 20% 90%, rgba(120,90,50,0.18), transparent 60%)";
      paper.style.clipPath = torn(r);
      paper.style.padding = `${(size * 0.06).toFixed(0)}px ${(size * 0.24).toFixed(0)}px ${(size * 0.1).toFixed(0)}px`;
      paper.style.lineHeight = "1.05";
      const span = el("span", "", paper, st.upper ? text.toUpperCase() : text);
      span.style.fontFamily = st.family;
      span.style.fontSize = size + "px";
      span.style.color = st.color;
      span.style.display = "inline-block";
      if (st.family === MARKER) span.style.letterSpacing = "0.02em";
      if (r() < 0.3) {
        const tape = el("div", "abs", box);
        tape.style.width = `${(size * 0.75).toFixed(0)}px`;
        tape.style.height = `${(size * 0.22).toFixed(0)}px`;
        tape.style.background = "rgba(205,200,185,0.55)";
        tape.style.left = r() < 0.5 ? "-8%" : "70%";
        tape.style.top = "-10%";
        tape.style.transform = `rotate(${((r() - 0.5) * 50).toFixed(1)}deg)`;
      }
      return { box, paper, span };
    }

    const SVGNS = "http://www.w3.org/2000/svg";
    function svg(tag, attrs, parent) {
      const n = document.createElementNS(SVGNS, tag);
      Object.entries(attrs).forEach(([k, v]) => n.setAttribute(k, String(v)));
      if (parent) parent.appendChild(n);
      return n;
    }

    function stroke(parent, d, width = 8) {
      const path = svg("path", { d, fill: "none", stroke: RED, "stroke-width": width, "stroke-linecap": "round", "stroke-linejoin": "round" }, parent);
      const length = path.getTotalLength ? path.getTotalLength() : 600;
      path.style.strokeDasharray = `${length}`;
      path.style.strokeDashoffset = `${length}`;
      path._length = length;
      return path;
    }

    // Lays the words out in staggered rows inside the zone, as big as fits.
    function layout(items, zone, align, r) {
      const [x0, y0, x1, y1] = zone;
      const width = x1 - x0;
      const height = y1 - y0;
      let scale = 1;
      let rows = [];
      for (let attempt = 0; attempt < 14; attempt++) {
        items.forEach((it) => {
          it.span.style.fontSize = `${(it.base * scale).toFixed(1)}px`;
          it.w = it.box.offsetWidth;
          it.h = it.box.offsetHeight;
        });
        rows = [];
        let row = [];
        let used = 0;
        items.forEach((it) => {
          const gap = row.length ? -it.h * OVERLAP : 0;
          if (row.length && (used + gap + it.w > width || row.length >= 3 || (it.role === "key" && it.w > width * 0.55))) {
            rows.push(row);
            row = [];
            used = 0;
          }
          row.push(it);
          used += (row.length > 1 ? -it.h * OVERLAP : 0) + it.w;
        });
        if (row.length) rows.push(row);
        const total = rows.reduce((a, rw) => a + Math.max(...rw.map((it) => it.h)) * ROW, 0);
        const widest = Math.max(...rows.map((rw) => rw.reduce((a, it, i) => a + it.w - (i ? it.h * OVERLAP : 0), 0)));
        if (total <= height && widest <= width) break;
        scale *= 0.9;
      }
      let y = y0;
      rows.forEach((rw, ri) => {
        const rowH = Math.max(...rw.map((it) => it.h));
        const rowW = rw.reduce((a, it, i) => a + it.w - (i ? it.h * OVERLAP : 0), 0);
        const stagger = (ri % 2 ? 1 : -1) * Math.min(70, (width - rowW) / 2) * (0.4 + r() * 0.6);
        let x = align === "left" ? x0 + Math.max(0, Math.min(width - rowW, (ri % 3) * 40 + r() * 30)) : x0 + (width - rowW) / 2 + stagger;
        x = Math.max(x0, Math.min(x1 - rowW, x));
        rw.forEach((it, i) => {
          if (i) x -= it.h * OVERLAP;
          it.x = x;
          it.y = y + (rowH - it.h) / 2 + (r() - 0.5) * rowH * 0.1;
          it.rot = (r() - 0.5) * 14;
          x += it.w;
        });
        y += rowH * ROW;
      });
      if (align === "middle") {
        const shift = Math.max(0, (y1 - y) / 2 - 20);
        rows.forEach((rw) => rw.forEach((it) => (it.y += shift)));
        y += shift;
      }
      return { scale, bottom: y, rows };
    }

    // ---- one scene ----------------------------------------------------------------------

    async function buildScene(scene, index, data, ctx) {
      const v = scene.visual || {};
      const r = rng(hash(`${data.title}|${index}|${v.text || ""}`));
      const root = el("div", "scene", ctx.layer);
      root.style.overflow = "hidden";
      root.style.display = "block"; // laid out while it is built: words are measured
      const shot0 = v.shot || "hero";
      const photos = ctx.photos;
      const cutPhotos = photos.filter((p) => p.cut);
      let shot = shot0;
      if (shot === "painted" && !v.image) shot = "hero"; // no painting here: the photo stands in
      const needsCut = !["print", "words", "painted"].includes(shot);
      if (needsCut && !cutPhotos.length) shot = "print";
      const pool = shot === "print" ? photos.filter((p) => p.img) : cutPhotos;
      const photo = pool.length ? pool[ctx.turn++ % pool.length] : null;

      // backdrop
      const back = canvas(BW, BH);
      const bctx = back.getContext("2d");
      const kinds = BACKDROPS[shot] || ["wall"];
      const kind = kinds[Math.floor(r() * kinds.length)];
      if (v.image && ctx.paintings[v.image]) paintedBackdrop(bctx, ctx.paintings[v.image]);
      else if (kind === "photo") photoBackdrop(bctx, (photo && photo.img) || (photos[0] && photos[0].img), r);
      else if (kind === "sky") skyBackdrop(bctx, r);
      else if (kind === "crowd") crowdBackdrop(bctx, r);
      else wallBackdrop(bctx, r);
      const bgWrap = el("div", "abs", root);
      place(bgWrap, 0, 0, W, H);
      back.style.width = W + "px";
      back.style.height = H + "px";
      back.style.transformOrigin = "50% 45%";
      bgWrap.appendChild(back);

      // the singer
      const subject = el("div", "abs", root);
      place(subject, 0, 0, W, H);
      const actors = [];
      let face = [600, 1040, 230];
      const addCut = async (look, spot, opacity = 1, flip = false, clip = null) => {
        const art = await graded(photo, look);
        const k = spot.s / art.scale;
        const dx = spot.x - art.pad * k;
        const dy = spot.y - art.pad * k;
        const dw = art.width * k;
        const dh = art.height * k;
        // only what can come on screen (with room for the camera's push)
        const x0 = Math.max(dx, -90);
        const y0 = Math.max(dy, -90);
        const x1 = Math.min(dx + dw, W + 90);
        const y1 = Math.min(dy + dh, H + 90);
        if (x1 <= x0 || y1 <= y0) return null;
        const node = canvas(x1 - x0, y1 - y0);
        node.getContext("2d").drawImage(art.canvas, (x0 - dx) / k, (y0 - dy) / k, (x1 - x0) / k, (y1 - y0) / k, 0, 0, node.width, node.height);
        node.className = "abs";
        (clip || subject).appendChild(node);
        place(node, x0, y0, x1 - x0, y1 - y0);
        node.style.opacity = String(opacity);
        node.style.transformOrigin = `${(spot.face[0] - x0).toFixed(0)}px ${(spot.face[1] - y0).toFixed(0)}px`;
        const base = flip ? "scaleX(-1)" : "";
        node.style.transform = base;
        actors.push({ node, base, depth: opacity < 1 ? 0.5 : 1 });
        return node;
      };
      let printBox = null;
      if (shot === "print" && photo && photo.img) {
        const img = photo.img;
        const w = 660;
        const h = Math.min(980, (w * img.height) / img.width);
        printBox = el("div", "abs", subject);
        place(printBox, 540 - w / 2 - 22, 1240 - h / 2 - 22, w + 44, h + 70);
        printBox.style.background = "#f1ebde";
        printBox.style.clipPath = torn(r, 0.5);
        printBox.style.filter = "drop-shadow(0 18px 22px rgba(0,0,0,0.6))";
        printBox.style.transform = `rotate(${((r() - 0.5) * 9).toFixed(1)}deg)`;
        const inner = canvas(w, h);
        const ictx = inner.getContext("2d");
        ictx.filter = "sepia(0.35) contrast(1.08) saturate(0.95)";
        const s = Math.max(w / img.width, h / img.height);
        // a tall photo shows its top (where the face is) rather than its middle
        ictx.drawImage(img, (w - img.width * s) / 2, (h - img.height * s) * 0.15, img.width * s, img.height * s);
        inner.className = "abs";
        place(inner, 22, 22, w, h);
        printBox.appendChild(inner);
        const tape = el("div", "abs", printBox);
        place(tape, w / 2 - 70, -4, 180, 46);
        tape.style.background = "rgba(210,205,190,0.6)";
        tape.style.transform = "rotate(-3deg)";
        face = [540, 1240 - h / 4, 200];
        actors.push({ node: printBox, base: printBox.style.transform, depth: 1 });
      } else if (photo && photo.cutImg && shot !== "words" && shot !== "painted") {
        const spot = placement(shot, photo, photo.cutImg);
        face = spot.face;
        if (shot === "mirror") {
          const frame = el("div", "abs", subject);
          place(frame, 590, 520, 400, 1120);
          frame.style.border = "18px solid #b8862f";
          frame.style.borderImage = "linear-gradient(135deg, #f3d27a, #8a5a1c 40%, #e8c063 60%, #6e4513) 1";
          frame.style.boxShadow = "0 0 60px rgba(255,190,90,0.55), inset 0 0 40px rgba(0,0,0,0.8)";
          frame.style.background = "rgba(30,20,12,0.85)";
          frame.style.overflow = "hidden";
          const inside = el("div", "abs", frame);
          place(inside, -608, -538, W, H);
          const mirrored = placement("mirror", photo, photo.cutImg);
          mirrored.s *= 0.95;
          mirrored.x = 800 - (mirrored.face[0] - mirrored.x) * 0.95;
          mirrored.y = 1080 - (mirrored.face[1] - mirrored.y) * 0.95;
          mirrored.face = [800, 1080, mirrored.face[2] * 0.95];
          await addCut("mirror", mirrored, 0.92, true, inside);
          const sheen = el("div", "abs", frame);
          place(sheen, 0, 0, 364, 1084);
          sheen.style.background = "linear-gradient(115deg, rgba(255,255,255,0.0) 30%, rgba(255,255,255,0.16) 42%, rgba(255,255,255,0) 54%)";
          await addCut("warm", spot);
        } else if (shot === "echo") {
          const side = (dx) => {
            const p = placement("echo", photo, photo.cutImg);
            const k = 0.82;
            p.s *= k;
            p.x = 540 + dx - (p.face[0] - p.x) * k;
            p.y = 1120 - (p.face[1] - p.y) * k;
            p.face = [540 + dx, 1120, p.face[2] * k];
            return p;
          };
          await addCut("faded", side(-300), 0.5);
          await addCut("faded", side(300), 0.5);
          await addCut("warm", spot);
        } else if (shot === "silhouette") {
          await addCut("dark", spot);
        } else {
          if (spot.full) {
            const feet = spot.y + photo.cutImg.height * spot.s * 0.985;
            const width = photo.cutImg.width * spot.s * 1.3;
            const pool = el("div", "abs", subject);
            place(pool, spot.face[0] - width, feet - 60, width * 2, 120);
            pool.style.background = "radial-gradient(ellipse at 50% 50%, rgba(255,170,80,0.35), rgba(255,140,60,0) 70%)";
            const shadow = el("div", "abs", subject);
            place(shadow, spot.face[0] - width * 0.45, feet - 16, width * 0.9, 34);
            shadow.style.background = "radial-gradient(ellipse at 50% 50%, rgba(0,0,0,0.75), rgba(0,0,0,0) 70%)";
          }
          await addCut("warm", spot);
        }
        if (!spot.full && shot === "far") face = spot.face;
      }

      // words
      const words = (scene.words || []).filter((w) => String(w.text || "").trim());
      const keys = new Set(String(v.highlight || "").split(/\s+/).map(plain).filter(Boolean));
      const theme = ["mixed", "caps", "mixed", "serif"][Math.floor(r() * 4)];
      const wordLayer = el("div", "abs", root);
      place(wordLayer, 0, 0, W, H);
      const [zone, align] = ZONES[shot] || ZONES.hero;
      const items = words.map((w, k) => {
        const key = keys.has(plain(w.text));
        const role = key ? "key" : SMALL.has(plain(w.text)) ? "small" : "normal";
        const st = styleOf(role, k + index, theme, r);
        const base = BASE[role] * (shot === "words" ? 1.6 : 1);
        const made = sticker(wordLayer, String(w.text).trim(), st, base, r);
        return { ...made, word: w, role, base, st };
      });
      const laid = items.length ? layout(items, zone, align, r) : { bottom: zone[1], rows: [] };
      items.forEach((it) => {
        place(it.box, it.x, it.y);
        it.box.style.display = "none";
      });
      const underlines = items
        .filter((it) => it.st.underline)
        .map((it) => {
          const art = svg("svg", { width: it.w + 40, height: 60, viewBox: `0 0 ${it.w + 40} 60` }, wordLayer);
          art.style.position = "absolute";
          art.style.left = `${(it.x - 20).toFixed(0)}px`;
          art.style.top = `${(it.y + it.h - 22).toFixed(0)}px`;
          art.style.overflow = "visible";
          const d = `M 20 ${30 + (r() - 0.5) * 8} Q ${(it.w + 40) / 2} ${44 + r() * 10} ${it.w + 20} ${24 + (r() - 0.5) * 8}`;
          return { art, path: stroke(art, d, 7), at: it.word.start + 0.14 };
        });

      // side notes: in the room the words leave, near them, never on a word or the face
      const notes = (v.items || []).map((n) => String(n.text || "").trim()).filter(Boolean).slice(0, 3);
      const [fx0, fy0, fs0] = face;
      const taken = items.map((it) => [it.x - 12, it.y - 12, it.x + it.w + 12, it.y + it.h + 12]);
      if (shot !== "words" && shot !== "painted") taken.push([fx0 - fs0 * 0.95, fy0 - fs0 * 1.15, fx0 + fs0 * 0.95, fy0 + fs0 * 1.1]);
      const blockLeft = items.length ? Math.min(...items.map((it) => it.x)) : zone[0];
      const blockRight = items.length ? Math.max(...items.map((it) => it.x + it.w)) : zone[2];
      const free = (box) => !taken.some((b) => box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1]);
      const noteItems = [];
      notes.forEach((text, k) => {
        const st = { family: SCRIPT, color: "#2a1d12", paper: k % 2 ? "#e9d9a8" : "#efe7d6", upper: false };
        const made = sticker(wordLayer, text, st, 60, r);
        const w = made.box.offsetWidth;
        const h = made.box.offsetHeight;
        // the nearest free place to a corner of the words (alternating sides), in the safe area
        const aimX = k % 2 ? blockRight - w : blockLeft;
        const aimY = laid.bottom + 10;
        let best = null;
        for (let y = 250; y <= 1440 - h; y += 30) {
          for (let x = 60; x <= 930 - w; x += 30) {
            const box = [x, y, x + w, y + h];
            if (!free(box)) continue;
            const d = Math.hypot(x - aimX, (y - aimY) * 1.4);
            if (!best || d < best[0]) best = [d, x, y];
          }
        }
        if (!best) {
          made.box.remove();
          return;
        }
        taken.push([best[1] - 10, best[2] - 10, best[1] + w + 10, best[2] + h + 10]);
        place(made.box, best[1], best[2]);
        made.box.style.display = "none";
        noteItems.push({ ...made, at: scene.start + 0.35 + k * 0.32, rot: (r() - 0.5) * 16 });
      });

      // doodle
      const doodles = el("div", "abs", wordLayer);
      place(doodles, 0, 0, W, H);
      const marks = [];
      const art = svg("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}` }, doodles);
      art.style.position = "absolute";
      art.style.left = "0";
      art.style.top = "0";
      const firstKey = items.find((it) => it.role === "key") || items[0];
      const doodle = { "❓": "question", "⭐": "star", "❤️": "heart", "➡️": "arrow", "❌": "cross", "👑": "crown", "✨": "spark" }[v.emoji] || "";
      const lastAt = words.length ? words[words.length - 1].start : scene.start + 0.4;
      const [fx, fy, fs] = face;
      if (doodle === "question") {
        [
          [fx - fs * 1.5, fy - fs * 0.2, -14],
          [fx + fs * 1.3, fy - fs * 0.6, 12],
          [fx + fs * 1.1, fy + fs * 0.9, 8],
        ].forEach(([x, y, rot], k) => {
          const q = el("div", "abs", doodles, "?");
          q.style.fontFamily = MARKER;
          q.style.fontSize = `${Math.round(90 + r() * 40)}px`;
          q.style.color = RED;
          q.style.textShadow = "0 0 14px rgba(255,255,255,0.5)";
          place(q, Math.max(40, Math.min(980, x)), Math.max(240, Math.min(1450, y)));
          q.style.display = "none";
          marks.push({ node: q, at: lastAt + 0.1 + k * 0.12, rot, pop: true });
        });
      } else if (doodle && firstKey) {
        const cx = firstKey.x;
        const cy = firstKey.y;
        const w = firstKey.w;
        const h = firstKey.h;
        let d = "";
        if (doodle === "star") {
          const sx = cx - 30;
          const sy = cy - 10;
          d = `M ${sx} ${sy - 46} L ${sx + 13} ${sy - 12} L ${sx + 48} ${sy - 12} L ${sx + 20} ${sy + 10} L ${sx + 31} ${sy + 44} L ${sx} ${sy + 22} L ${sx - 31} ${sy + 44} L ${sx - 20} ${sy + 10} L ${sx - 48} ${sy - 12} L ${sx - 13} ${sy - 12} Z`;
        } else if (doodle === "heart") {
          const sx = cx + w + 30;
          const sy = cy + 10;
          d = `M ${sx} ${sy + 40} C ${sx - 60} ${sy} ${sx - 30} ${sy - 40} ${sx} ${sy - 10} C ${sx + 30} ${sy - 40} ${sx + 60} ${sy} ${sx} ${sy + 40}`;
        } else if (doodle === "arrow") {
          const sx = cx + w * 0.5;
          const sy = laid.bottom + 10;
          const ex = fx + (fx > sx ? -fs : fs);
          const ey = Math.max(sy + 120, fy - fs * 1.2);
          d = `M ${sx} ${sy} Q ${(sx + ex) / 2 + 80} ${(sy + ey) / 2} ${ex} ${ey} M ${ex} ${ey} l -30 -26 M ${ex} ${ey} l 8 -38`;
        } else if (doodle === "cross") {
          const sx = cx + w + 20;
          const sy = cy;
          d = `M ${sx} ${sy} L ${sx + 70} ${sy + 70} M ${sx + 70} ${sy} L ${sx} ${sy + 70}`;
        } else if (doodle === "crown") {
          const sx = fx - fs * 0.7;
          const sy = fy - fs * 1.15;
          const w2 = fs * 1.4;
          d = `M ${sx} ${sy} L ${sx + w2 * 0.12} ${sy - 70} L ${sx + w2 * 0.35} ${sy - 25} L ${sx + w2 * 0.5} ${sy - 85} L ${sx + w2 * 0.65} ${sy - 25} L ${sx + w2 * 0.88} ${sy - 70} L ${sx + w2} ${sy} Z`;
        } else if (doodle === "spark") {
          const sx = cx + w / 2;
          const sy = cy + h / 2;
          for (let k = 0; k < 5; k++) {
            const a = -Math.PI * 0.9 + (k * Math.PI * 0.8) / 4;
            const r1 = Math.max(w, h) * 0.62;
            d += `M ${sx + Math.cos(a) * r1} ${sy + Math.sin(a) * r1 * 0.7} L ${sx + Math.cos(a) * (r1 + 46)} ${sy + Math.sin(a) * (r1 * 0.7 + 46)} `;
          }
        }
        if (d) marks.push({ path: stroke(art, d, 8), at: (firstKey.word.start || scene.start) + 0.2 });
      }

      // an intro title while the song plays before the first word
      const intro = [];
      if (index === 0 && words.length && words[0].start > 1.1 && (data.singer || {}).title) {
        const info = data.singer;
        const a = sticker(wordLayer, info.title, { family: MARKER, color: INK, paper: PAPER[0], upper: true }, 96, r);
        place(a.box, 110, 420);
        intro.push({ node: a.box, at: 0.15, until: words[0].start - 0.1, rot: -4 });
        if (info.artist) {
          const b = sticker(wordLayer, info.artist, { family: SCRIPT, color: RED, paper: PAPER[1], upper: false }, 84, r);
          place(b.box, 240, 420 + a.box.offsetHeight + 10);
          intro.push({ node: b.box, at: 0.45, until: words[0].start - 0.1, rot: 5 });
        }
      }

      let frame = null;
      if (r() < 0.34) {
        frame = tornFrame(r);
        frame.className = "abs";
        place(frame, 0, 0, W, H);
        frame.style.width = W + "px";
        frame.style.height = H + "px";
        root.insertBefore(frame, wordLayer);
      }

      const transition = index ? TRANSITIONS[(hash(data.title || "") + index * 7) % TRANSITIONS.length] : "none";
      const drift = (r() - 0.5) * 30;
      return { scene, root, back, subject, actors, items, underlines, noteItems, marks, intro, transition, drift, shot };
    }

    function updateScene(s, t) {
      const { scene } = s;
      const span = Math.max(0.6, scene.end - scene.start);
      const p = clamp((t - scene.start) / span);
      // the camera: a slow push, the backdrop slower than the singer
      s.back.style.transform = `scale(${(1.06 + 0.06 * p).toFixed(4)}) translate(${(s.drift * p * 0.5).toFixed(1)}px, ${(-8 * p).toFixed(1)}px)`;
      const push = 1 + 0.07 * p;
      s.actors.forEach((a) => {
        const k = 1 + (push - 1) * a.depth;
        a.node.style.transform = `${a.base} scale(${k.toFixed(4)})`.trim();
      });
      s.items.forEach((it) => {
        const at = it.word.start - 0.03;
        if (t < at) {
          it.box.style.display = "none";
          return;
        }
        it.box.style.display = "block";
        const q = prog(t, at, 0.16);
        const land = ease.back(q);
        const scale = 1 + 0.75 * (1 - land);
        const rot = it.rot + (1 - ease.out(q)) * 10;
        const bob = Math.sin((t - at) * 2.2 + it.x * 0.01) * 2;
        it.box.style.opacity = Math.min(1, q * 4).toFixed(3);
        it.box.style.transform = `translateY(${bob.toFixed(1)}px) rotate(${rot.toFixed(2)}deg) scale(${scale.toFixed(4)})`;
      });
      s.underlines.forEach((u) => {
        const q = ease.out(prog(t, u.at, 0.28));
        u.art.style.display = t >= u.at ? "block" : "none";
        u.path.style.strokeDashoffset = `${(u.path._length * (1 - q)).toFixed(1)}`;
      });
      s.noteItems.forEach((n) => {
        if (t < n.at) {
          n.box.style.display = "none";
          return;
        }
        n.box.style.display = "block";
        const q = ease.back(prog(t, n.at, 0.3));
        n.box.style.opacity = Math.min(1, prog(t, n.at, 0.12)).toFixed(3);
        n.box.style.transform = `rotate(${n.rot.toFixed(1)}deg) scale(${lerp(0.4, 1, q).toFixed(4)})`;
      });
      s.marks.forEach((m) => {
        if (m.path) {
          const q = ease.out(prog(t, m.at, 0.4));
          m.path.style.strokeDashoffset = `${(m.path._length * (1 - q)).toFixed(1)}`;
        } else {
          m.node.style.display = t >= m.at ? "block" : "none";
          const q = ease.back(prog(t, m.at, 0.25));
          const wob = Math.sin((t - m.at) * 3) * 4;
          m.node.style.transform = `rotate(${(m.rot + wob).toFixed(1)}deg) scale(${lerp(0.2, 1, q).toFixed(4)})`;
        }
      });
      s.intro.forEach((n) => {
        const on = t >= n.at && t < n.until + 0.15;
        n.node.style.display = on ? "block" : "none";
        if (!on) return;
        const q = ease.back(prog(t, n.at, 0.3));
        const out = prog(t, n.until, 0.15);
        n.node.style.opacity = (Math.min(1, q * 3) * (1 - out)).toFixed(3);
        n.node.style.transform = `rotate(${n.rot}deg) scale(${lerp(1.6, 1, q).toFixed(4)})`;
      });
    }

    // ---- over everything: grain, dust, light, flashes, tears ------------------------------

    function overlays(stage, seed) {
      const top = el("div", "layer", stage);
      top.style.pointerEvents = "none";
      const grains = [];
      for (let k = 0; k < 6; k++) {
        const c = canvas(BW, BH);
        const ctx = c.getContext("2d");
        const img = ctx.createImageData(BW, BH);
        const r = rng(seed + k * 101);
        for (let i = 0; i < img.data.length; i += 4) {
          const v = Math.floor(r() * 255);
          img.data[i] = v;
          img.data[i + 1] = v;
          img.data[i + 2] = v;
          img.data[i + 3] = r() < 0.4 ? 14 : 0;
        }
        ctx.putImageData(img, 0, 0);
        c.className = "abs";
        c.style.width = W + "px";
        c.style.height = H + "px";
        c.style.left = "0";
        c.style.top = "0";
        c.style.display = "none";
        top.appendChild(c);
        grains.push(c);
      }
      const dust = canvas(W, H);
      dust.className = "abs";
      dust.style.left = "0";
      dust.style.top = "0";
      top.appendChild(dust);
      const dctx = dust.getContext("2d");
      const r = rng(seed + 7);
      const motes = Array.from({ length: 40 }, () => ({
        x: r() * W,
        y: r() * H,
        size: 1.5 + r() * 3.5,
        speed: 8 + r() * 30,
        sway: r() * Math.PI * 2,
        glow: r() < 0.35,
      }));
      const leak = el("div", "abs", top);
      place(leak, -400, -300, 1300, 1300);
      leak.style.background = "radial-gradient(circle, rgba(255,150,60,0.22), rgba(255,120,40,0.08) 45%, rgba(0,0,0,0) 70%)";
      const shade = el("div", "abs", top);
      place(shade, 0, 0, W, H);
      shade.style.background = "radial-gradient(ellipse at 50% 45%, rgba(0,0,0,0) 55%, rgba(5,2,0,0.55) 100%)";
      const flash = el("div", "abs", top);
      place(flash, 0, 0, W, H);
      flash.style.background = "#fff3dc";
      flash.style.opacity = "0";
      const tear = el("div", "abs", top);
      place(tear, -40, 0, W + 80, H + 300);
      tear.style.background = "#17100b";
      tear.style.clipPath = torn(rng(seed + 3), 0.6);
      tear.style.display = "none";
      const scan = el("div", "abs", top);
      place(scan, 0, 0, W, H);
      scan.style.background = "repeating-linear-gradient(0deg, rgba(255,255,255,0.06) 0 2px, rgba(0,0,0,0) 2px 6px)";
      scan.style.display = "none";
      const fade = el("div", "abs", top);
      place(fade, 0, 0, W, H);
      fade.style.background = "#000";
      fade.style.opacity = "0";
      return { grains, dctx, motes, leak, flash, tear, scan, fade };
    }

    async function load(stage, data, options) {
      await loadFonts(options.fontBase);
      const seed = hash(data.title || "singer");
      const layer = el("div", "layer", stage);
      const base = options.imageBase || "";
      const info = data.singer || {};
      const photos = await Promise.all(
        (info.photos || []).map(async (p) => ({
          ...p,
          img: await picture(p.image ? base + p.image : ""),
          cutImg: await picture(p.cutout ? base + p.cutout : ""),
          looks: {},
        })),
      );
      photos.forEach((p) => (p.cut = Boolean(p.cutImg)));
      const paintings = {};
      await Promise.all(
        (data.scenes || []).map(async (s) => {
          const name = s.visual && s.visual.image;
          if (name && !paintings[name]) paintings[name] = await picture(base + name);
        }),
      );
      const ctx = { layer, photos, paintings, turn: 0 };
      const scenes = [];
      // one at a time: a scene's photo turn depends on the ones before it
      for (let i = 0; i < (data.scenes || []).length; i++) scenes.push(await buildScene(data.scenes[i], i, data, ctx));
      const over = overlays(stage, seed);
      scenes.forEach((s) => (s.root.style.display = "none"));
      return {
        seek(t) {
          let shake = 0;
          for (let i = 0; i < scenes.length; i++) {
            const s = scenes[i];
            const { start, end } = s.scene;
            const next = scenes[i + 1];
            const lead = next && next.transition === "whip" ? 0.1 : next && next.transition === "glitch" ? 0.12 : 0;
            const last = i === scenes.length - 1;
            const on = t >= start - 0.001 && (last || t < end + lead);
            s.root.style.display = on ? "block" : "none";
            if (!on) continue;
            updateScene(s, t);
            let transform = "";
            let filter = "";
            // leaving: the next line's transition starts on this one
            if (next && t >= end - lead && lead) {
              const k = clamp((t - (end - lead)) / lead);
              if (next.transition === "whip") {
                transform = `translateY(${(-260 * ease.inOut(k)).toFixed(1)}px)`;
                filter = `blur(${(16 * k).toFixed(1)}px)`;
              } else if (next.transition === "glitch") {
                const j = Math.sin(t * 190) * 26 * k;
                transform = `translateX(${j.toFixed(1)}px)`;
                filter = `saturate(${(1 + 2 * k).toFixed(2)}) hue-rotate(${(k * 40).toFixed(0)}deg)`;
              }
            }
            // arriving
            const since = t - start;
            if (i && since < 0.32) {
              if (s.transition === "whip" && since < 0.14) {
                const k = 1 - since / 0.14;
                transform = `translateY(${(300 * k * k).toFixed(1)}px)`;
                filter = `blur(${(16 * k).toFixed(1)}px)`;
              } else if (s.transition === "punch") {
                const k = ease.out(clamp(since / 0.3));
                transform = `scale(${lerp(1.35, 1, k).toFixed(4)})`;
                if (since < 0.08) filter = `blur(${(6 * (1 - since / 0.08)).toFixed(1)}px)`;
              } else if (s.transition === "flash" || s.transition === "tear") {
                const k = ease.out(clamp(since / 0.25));
                transform = `scale(${lerp(1.08, 1, k).toFixed(4)})`;
              } else if (s.transition === "glitch" && since < 0.08) {
                transform = `translateX(${(Math.sin(t * 230) * 22).toFixed(1)}px)`;
              }
            }
            s.root.style.transform = transform;
            s.root.style.filter = filter;
            // each word landing shakes the frame a little
            s.items.forEach((it) => {
              const d = t - (it.word.start - 0.03);
              if (d >= 0 && d < 0.14) shake = Math.max(shake, (1 - d / 0.14) * (it.role === "key" ? 7 : 4));
            });
          }
          const ox = Math.sin(t * 97) * shake;
          const oy = Math.cos(t * 83) * shake;
          layer.style.transform = shake ? `translate(${ox.toFixed(1)}px, ${oy.toFixed(1)}px)` : "";
          // flashes and tears over the cut
          let flashOn = 0;
          let tearAt = null;
          let scanOn = false;
          scenes.forEach((s, i) => {
            if (!i) return;
            const d = t - s.scene.start;
            if (s.transition === "flash" && d > -0.02 && d < 0.2) flashOn = Math.max(flashOn, 0.85 * (1 - clamp(d / 0.2)));
            if (s.transition === "punch" && d > -0.01 && d < 0.1) flashOn = Math.max(flashOn, 0.3 * (1 - clamp(d / 0.1)));
            if (s.transition === "tear" && d > -0.14 && d < 0.14) tearAt = (d + 0.14) / 0.28;
            if (s.transition === "glitch" && d > -0.12 && d < 0.08) scanOn = true;
          });
          over.flash.style.opacity = flashOn.toFixed(3);
          over.tear.style.display = tearAt === null ? "none" : "block";
          if (tearAt !== null) over.tear.style.transform = `translateY(${lerp(H, -H - 300, ease.inOut(tearAt)).toFixed(0)}px) rotate(-4deg)`;
          over.scan.style.display = scanOn ? "block" : "none";
          const frameNo = Math.floor(t * 12);
          over.grains.forEach((g, k) => (g.style.display = k === frameNo % over.grains.length ? "block" : "none"));
          const ctx2 = over.dctx;
          ctx2.clearRect(0, 0, W, H);
          over.motes.forEach((m) => {
            const y = (((m.y - t * m.speed) % H) + H) % H;
            const x = m.x + Math.sin(t * 0.8 + m.sway) * 18;
            ctx2.fillStyle = m.glow ? "rgba(255,190,110,0.75)" : "rgba(255,235,210,0.35)";
            ctx2.beginPath();
            ctx2.arc(x, y, m.size, 0, Math.PI * 2);
            ctx2.fill();
          });
          over.leak.style.transform = `translate(${(Math.sin(t * 0.37) * 220).toFixed(0)}px, ${(Math.cos(t * 0.23) * 260).toFixed(0)}px)`;
          over.leak.style.opacity = (0.6 + 0.4 * Math.sin(t * 0.9)).toFixed(3);
          over.fade.style.opacity = clamp((t - (data.duration - 0.3)) / 0.3).toFixed(3);
        },
      };
    }

    return { load };
  }

  window.PlayerStyles = window.PlayerStyles || {};
  window.PlayerStyles.singer = { install };
})();
