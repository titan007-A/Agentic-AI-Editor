/** A job's progress as a snowman being built: the base, then the body, the head, and at
 * the end the hat, scarf and carrot; snow falls around it while the job runs. */
export function Snowman({
  value,
  size = 96,
  falling = true,
  label,
}: {
  /** 0 to 1 */
  value: number;
  size?: number;
  falling?: boolean;
  label?: string;
}) {
  const v = Math.max(0, Math.min(1, value));
  // Each part grows in over its share of the job.
  const part = (from: number, to: number) => Math.max(0, Math.min(1, (v - from) / (to - from)));
  const base = part(0, 0.35);
  const body = part(0.35, 0.65);
  const head = part(0.65, 0.88);
  const dress = part(0.88, 1);
  const snow = "var(--field)";
  const line = "var(--ink)";
  return (
    <span
      className="relative inline-block shrink-0"
      style={{ width: size, height: size }}
      role="progressbar"
      aria-label={label ?? "Progress"}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(v * 100)}
      data-testid="snowman"
    >
      {falling && <span className="snowman-snow" aria-hidden />}
      <svg viewBox="0 0 100 100" width={size} height={size} aria-hidden>
        <path
          d="M6 94 C 30 90, 70 92, 94 94"
          fill="none"
          stroke={line}
          strokeWidth="2"
          strokeLinecap="round"
        />
        {base > 0 && (
          <circle
            cx="50"
            cy={92 - 20 * base}
            r={20 * base}
            fill={snow}
            stroke={line}
            strokeWidth="2"
          />
        )}
        {body > 0 && (
          <circle
            cx="50"
            cy={56 - 4 * (1 - body)}
            r={14 * body}
            fill={snow}
            stroke={line}
            strokeWidth="2"
          />
        )}
        {head > 0 && (
          <g>
            <circle cx="50" cy="32" r={10 * head} fill={snow} stroke={line} strokeWidth="2" />
            {head === 1 && (
              <>
                <circle cx="46.5" cy="30" r="1.4" fill={line} />
                <circle cx="53.5" cy="30" r="1.4" fill={line} />
              </>
            )}
          </g>
        )}
        {body === 1 && (
          <g fill={line}>
            <circle cx="50" cy="50" r="1.3" />
            <circle cx="50" cy="56" r="1.3" />
            <circle cx="50" cy="62" r="1.3" />
          </g>
        )}
        {dress > 0 && (
          <g opacity={dress}>
            <path d="M51 33 L62 35 L51 36 Z" fill="var(--tangerine)" />
            <path
              d="M40 42 C 46 45, 54 45, 60 42 L 61 46 C 55 49, 45 49, 39 46 Z"
              fill="var(--brick)"
              stroke={line}
              strokeWidth="1"
            />
            <path d="M57 46 L 60 56 L 55 56 Z" fill="var(--brick)" stroke={line} strokeWidth="1" />
            <rect x="41" y="19" width="18" height="3" rx="1" fill={line} />
            <rect x="44" y="8" width="12" height="12" rx="1.5" fill={line} />
            <rect x="44" y="15" width="12" height="2.5" fill="var(--teal)" />
          </g>
        )}
      </svg>
    </span>
  );
}
