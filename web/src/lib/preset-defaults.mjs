// Plain JavaScript so node --test can run it without a build step.

/**
 * A new preset with every setting at the server's default, from the preset JSON schema.
 * Lengths have no default, so it starts at 15-60 seconds.
 * @param {{ properties?: Record<string, { default?: unknown }> }} schema
 * @param {string} name
 * @returns {import("./api").Preset}
 */
export function blankPreset(schema, name) {
  /** @type {Record<string, unknown>} */
  const preset = {};
  for (const [key, property] of Object.entries(schema.properties ?? {})) {
    if ("default" in property) preset[key] = structuredClone(property.default);
  }
  return { ...preset, name, label: name, min_duration: 15, max_duration: 60 };
}
