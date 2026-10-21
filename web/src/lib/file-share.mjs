// Plain JavaScript so node --test can run it without a build step.

/** @param {string} name */
export function videoFileName(name) {
  return (
    (name
      .replace(/[<>:"/\\|?*\x00-\x1f]/g, "_")
      .replace(/\.mp4$/i, "")
      .trim()
      .slice(0, 100) || "edited-video") + ".mp4"
  );
}

/**
 * Whether this browser can really hand a file to the OS share sheet. canShare({files})
 * alone is not enough: desktop Chrome and Edge say yes and then reject share() with
 * NotAllowedError. Android and iOS implement file sharing, desktop browsers do not.
 * @param {any} browser navigator, or a stand-in in tests
 */
export function canShareFiles(browser) {
  if (!browser?.share || !browser?.canShare) return false;
  const mobile = browser.userAgentData?.mobile;
  if (mobile === false) return false;
  if (mobile === true) return true;
  return /Android|iPhone|iPad|iPod/i.test(browser.userAgent || "");
}

/**
 * Hands only the file to the share sheet, never a page URL. Call it synchronously as the
 * first thing in a tap handler: Android drops the tap's permission after any await or
 * re-render and refuses with NotAllowedError.
 * @param {any} browser
 * @param {File} file
 * @returns {Promise<void>}
 */
export function shareVideoFile(browser, file) {
  const payload = { files: [file] };
  if (!browser.canShare?.(payload) || !browser.share)
    throw new Error("File sharing is unavailable. Download and attach the MP4 instead.");
  return browser.share(payload);
}
