/** Fireworks across the whole screen for a few seconds, with `message` in the middle;
 * nothing when the person asked for less motion. */
export function fireworks(message: string, seconds = 6) {
  if (typeof window === "undefined") return;
  const still = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  const layer = document.createElement("div");
  layer.className = "fireworks-layer";
  layer.setAttribute("role", "status");
  const text = document.createElement("p");
  text.className = "fireworks-message";
  text.textContent = message;
  layer.appendChild(text);
  document.body.appendChild(layer);
  const end = () => layer.remove();
  if (still) {
    window.setTimeout(end, 3500);
    return;
  }
  const canvas = document.createElement("canvas");
  layer.prepend(canvas);
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const ratio = window.devicePixelRatio || 1;
  const size = () => {
    canvas.width = innerWidth * ratio;
    canvas.height = innerHeight * ratio;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  };
  size();
  const colours = ["#ffd166", "#ef8148", "#4fb3b0", "#f2eadb", "#e9b949", "#ff6f86"];
  type Spark = { x: number; y: number; vx: number; vy: number; life: number; colour: string };
  let sparks: Spark[] = [];
  const burst = () => {
    const x = innerWidth * (0.15 + Math.random() * 0.7);
    const y = innerHeight * (0.15 + Math.random() * 0.4);
    const colour = colours[Math.floor(Math.random() * colours.length)];
    for (let i = 0; i < 70; i++) {
      const angle = (Math.PI * 2 * i) / 70;
      const speed = 2 + Math.random() * 3.5;
      sparks.push({
        x,
        y,
        vx: Math.cos(angle) * speed,
        vy: Math.sin(angle) * speed,
        life: 1,
        colour,
      });
    }
  };
  const started = performance.now();
  let next = 0;
  const frame = (now: number) => {
    const t = (now - started) / 1000;
    if (t > seconds && !sparks.length) return end();
    if (t < seconds && now > next) {
      burst();
      next = now + 350 + Math.random() * 450;
    }
    ctx.globalCompositeOperation = "destination-out";
    ctx.fillStyle = "rgba(0,0,0,0.25)";
    ctx.fillRect(0, 0, innerWidth, innerHeight);
    ctx.globalCompositeOperation = "lighter";
    sparks = sparks.filter((s) => s.life > 0);
    for (const s of sparks) {
      s.x += s.vx;
      s.y += s.vy;
      s.vy += 0.05;
      s.vx *= 0.985;
      s.life -= 0.012;
      ctx.globalAlpha = Math.max(0, s.life);
      ctx.fillStyle = s.colour;
      ctx.beginPath();
      ctx.arc(s.x, s.y, 2.2, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
    requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}
