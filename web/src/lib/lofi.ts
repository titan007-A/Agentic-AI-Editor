/** A lofi loop made on the page (Web Audio, no files, no copyright): four jazzy chords as
 * soft pads, a lazy 72 bpm beat with swing, a few pentatonic notes, and vinyl crackle,
 * through a warm low-pass. start() and stop(); it keeps going until stopped. */
const BPM = 72;
const BEAT = 60 / BPM;
// Fmaj7 - Em7 - Dm7 - Cmaj7 (and back), as MIDI notes.
const CHORDS = [
  [53, 57, 60, 64],
  [52, 55, 59, 62],
  [50, 53, 57, 60],
  [48, 52, 55, 59],
];
const PENTA = [69, 72, 74, 76, 79, 81];

let ctx: AudioContext | null = null;
let out: GainNode | null = null;
let timer: number | undefined;
let crackle: AudioBufferSourceNode | null = null;
let nextBar = 0;
let bar = 0;

const hz = (midi: number) => 440 * 2 ** ((midi - 69) / 12);

function noise(seconds: number): AudioBuffer {
  const buffer = ctx!.createBuffer(1, Math.ceil(ctx!.sampleRate * seconds), ctx!.sampleRate);
  const data = buffer.getChannelData(0);
  for (let i = 0; i < data.length; i++) data[i] = Math.random() * 2 - 1;
  return buffer;
}

function pad(midi: number, at: number, length: number) {
  const osc = ctx!.createOscillator();
  const gain = ctx!.createGain();
  osc.type = "triangle";
  osc.frequency.value = hz(midi);
  osc.detune.value = (Math.random() - 0.5) * 12;
  gain.gain.setValueAtTime(0, at);
  gain.gain.linearRampToValueAtTime(0.05, at + 0.4);
  gain.gain.setValueAtTime(0.05, at + length - 0.6);
  gain.gain.linearRampToValueAtTime(0, at + length);
  osc.connect(gain).connect(out!);
  osc.start(at);
  osc.stop(at + length + 0.05);
}

function kick(at: number) {
  const osc = ctx!.createOscillator();
  const gain = ctx!.createGain();
  osc.frequency.setValueAtTime(110, at);
  osc.frequency.exponentialRampToValueAtTime(45, at + 0.18);
  gain.gain.setValueAtTime(0.35, at);
  gain.gain.exponentialRampToValueAtTime(0.001, at + 0.3);
  osc.connect(gain).connect(out!);
  osc.start(at);
  osc.stop(at + 0.32);
}

function tick(at: number, loud: number, high: number) {
  const src = ctx!.createBufferSource();
  src.buffer = noise(0.2);
  const filter = ctx!.createBiquadFilter();
  filter.type = "bandpass";
  filter.frequency.value = high;
  const gain = ctx!.createGain();
  gain.gain.setValueAtTime(loud, at);
  gain.gain.exponentialRampToValueAtTime(0.001, at + 0.12);
  src.connect(filter).connect(gain).connect(out!);
  src.start(at);
}

function note(midi: number, at: number) {
  const osc = ctx!.createOscillator();
  const gain = ctx!.createGain();
  osc.type = "sine";
  osc.frequency.value = hz(midi);
  gain.gain.setValueAtTime(0, at);
  gain.gain.linearRampToValueAtTime(0.04, at + 0.02);
  gain.gain.exponentialRampToValueAtTime(0.001, at + 1.2);
  osc.connect(gain).connect(out!);
  osc.start(at);
  osc.stop(at + 1.3);
}

function scheduleBar(at: number) {
  const chord = CHORDS[bar % CHORDS.length];
  for (const m of chord) pad(m, at, BEAT * 4);
  pad(chord[0] - 12, at, BEAT * 4);
  const kicks = bar % 2 ? [0, 1.75, 2.5] : [0, 2.5];
  for (const k of kicks) kick(at + k * BEAT);
  for (let beat = 0; beat < 4; beat++) {
    const t = at + beat * BEAT;
    if (beat === 1 || beat === 3) tick(t, 0.12, 1800); // the snare's brush
    tick(t, 0.03, 7000);
    tick(t + BEAT * 0.58, 0.04, 7000); // swung hat
  }
  if (Math.random() < 0.7) {
    const steps = 2 + Math.floor(Math.random() * 3);
    for (let i = 0; i < steps; i++) {
      note(
        PENTA[Math.floor(Math.random() * PENTA.length)],
        at + BEAT * (i * 1.5 + Math.random() * 0.4),
      );
    }
  }
  bar += 1;
}

export function lofiPlaying(): boolean {
  return timer !== undefined;
}

export function startLofi(volume = 0.6) {
  if (timer !== undefined) return;
  ctx ??= new AudioContext();
  void ctx.resume();
  out = ctx.createGain();
  out.gain.value = volume;
  const warm = ctx.createBiquadFilter();
  warm.type = "lowpass";
  warm.frequency.value = 2400;
  out.connect(warm).connect(ctx.destination);
  crackle = ctx.createBufferSource();
  crackle.buffer = noise(4);
  crackle.loop = true;
  const dust = ctx.createBiquadFilter();
  dust.type = "highpass";
  dust.frequency.value = 3000;
  const dustGain = ctx.createGain();
  dustGain.gain.value = 0.012;
  crackle.connect(dust).connect(dustGain).connect(out);
  crackle.start();
  nextBar = ctx.currentTime + 0.1;
  timer = window.setInterval(() => {
    while (nextBar < ctx!.currentTime + 1.5) {
      scheduleBar(nextBar);
      nextBar += BEAT * 4;
    }
  }, 250);
}

export function stopLofi() {
  window.clearInterval(timer);
  timer = undefined;
  if (out && ctx) {
    out.gain.setTargetAtTime(0, ctx.currentTime, 0.3);
    const gone = out;
    window.setTimeout(() => gone.disconnect(), 1500);
  }
  crackle?.stop();
  crackle = null;
  out = null;
}
