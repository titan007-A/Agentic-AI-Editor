// Plain JavaScript so node --test can run it without a build step.

/**
 * The whole number typed into a field, or null while it is empty, partial or out of
 * range, so a field can be cleared and retyped without jumping to a default.
 * @param {string} text
 * @param {number} min
 * @param {number} max
 */
export function wholeNumber(text, min, max) {
  if (!/^\s*\d+\s*$/.test(text)) return null;
  const value = Number(text);
  return value >= min && value <= max ? value : null;
}
