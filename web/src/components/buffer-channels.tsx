"use client";

import { Checkbox } from "@/components/ui/checkbox";
import type { BufferChannel, BufferKey } from "@/lib/api";

/** Every channel the keys show, once each, with the key it is listed under: a Buffer
 * account reached by two keys is still one channel. */
export function channelsOf(keys: BufferKey[]): { channel: BufferChannel; key: BufferKey }[] {
  const seen = new Set<string>();
  const found: { channel: BufferChannel; key: BufferKey }[] = [];
  for (const key of keys) {
    for (const channel of key.channels) {
      if (seen.has(channel.id)) continue;
      seen.add(channel.id);
      found.push({ channel, key });
    }
  }
  return found;
}

/** What's wrong with posting to a channel, if anything. */
export function channelWarning(channel: BufferChannel): string | null {
  if (channel.broken) return "disconnected or locked in Buffer";
  if (channel.personal) return "personal Instagram: switch it to Creator or Business";
  if (channel.paused) return "queue paused";
  return null;
}

export function ChannelLine({ channel }: { channel: BufferChannel }) {
  const warning = channelWarning(channel);
  return (
    <span className="min-w-0 text-sm">
      {channel.name} <span className="text-muted-foreground">({channel.service})</span>
      {warning && (
        <span
          className={
            channel.paused && !channel.broken && !channel.personal
              ? "text-muted-foreground"
              : "text-destructive"
          }
        >
          {" "}
          · {warning}
        </span>
      )}
    </span>
  );
}

/** Ticks for channels, grouped by the key they come through. `only` limits the list. */
export function ChannelPicker({
  keys,
  chosen,
  onChange,
  only,
  idPrefix,
}: {
  keys: BufferKey[];
  chosen: string[];
  onChange: (next: string[]) => void;
  only?: (id: string) => boolean;
  idPrefix: string;
}) {
  const rows = channelsOf(keys).filter(({ channel }) => !only || only(channel.id));
  const groups = keys
    .map((key) => ({ key, rows: rows.filter((r) => r.key.id === key.id) }))
    .filter((g) => g.rows.length > 0);
  if (!groups.length) return <p className="text-sm text-muted-foreground">No channels.</p>;
  return (
    <div className="space-y-3">
      {groups.map(({ key, rows: inKey }) => (
        <fieldset key={key.id} className="space-y-2">
          {groups.length > 1 && (
            <legend className="mb-1 text-xs font-semibold text-muted-foreground">
              {key.label}
            </legend>
          )}
          {inKey.map(({ channel }) => (
            <label
              key={channel.id}
              htmlFor={`${idPrefix}-${channel.id}`}
              className="sketch-tile flex min-h-11 cursor-pointer items-center gap-3 p-2"
            >
              <Checkbox
                id={`${idPrefix}-${channel.id}`}
                checked={chosen.includes(channel.id)}
                onCheckedChange={(on) =>
                  onChange(
                    on === true
                      ? [...chosen, channel.id]
                      : chosen.filter((id) => id !== channel.id),
                  )
                }
              />
              {channel.avatar && (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={channel.avatar} alt="" className="size-7 shrink-0 rounded-full" />
              )}
              <ChannelLine channel={channel} />
            </label>
          ))}
        </fieldset>
      ))}
    </div>
  );
}
