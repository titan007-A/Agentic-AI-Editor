/** A short burst of paper bits and snowflakes from the top of the screen, in the page's
 * own colours; nothing when the person asked for less motion. */
export function confetti(pieces = 80) {
  if (typeof window === "undefined") return;
  if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
  const layer = document.createElement("div");
  layer.setAttribute("aria-hidden", "true");
  layer.className = "confetti-layer";
  const colours = [
    "var(--teal)",
    "var(--tangerine)",
    "var(--mustard)",
    "var(--olive)",
    "var(--brick)",
  ];
  for (let i = 0; i < pieces; i++) {
    const bit = document.createElement("span");
    const snow = i % 5 === 0;
    bit.className = snow ? "confetti-bit confetti-snow" : "confetti-bit";
    bit.textContent = snow ? "❄" : "";
    bit.style.left = `${Math.random() * 100}%`;
    bit.style.setProperty("--drift", `${(Math.random() - 0.5) * 160}px`);
    bit.style.setProperty("--spin", `${(Math.random() - 0.5) * 900}deg`);
    bit.style.animationDuration = `${1.6 + Math.random() * 1.6}s`;
    bit.style.animationDelay = `${Math.random() * 0.35}s`;
    if (!snow) bit.style.background = colours[i % colours.length];
    layer.appendChild(bit);
  }
  document.body.appendChild(layer);
  window.setTimeout(() => layer.remove(), 3800);
}
