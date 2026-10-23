// Plain JavaScript so node --test can run it without a build step.

const cancelled = () => new DOMException("The upload was cancelled.", "AbortError");

/**
 * Sends one file as multipart form data (field `file`) and reports how much of it has
 * gone, which fetch can't. Resolves with the response's status and JSON body (null when
 * it isn't JSON). Rejects with an AbortError when cancelled and a TypeError when the
 * connection drops.
 * @param {string} url
 * @param {Blob} file
 * @param {{ onProgress?: (fraction: number) => void, signal?: AbortSignal,
 *   XHR?: any, FormData?: any }} [options]
 * @returns {Promise<{ status: number, statusText: string, body: any }>}
 */
export function sendFile(url, file, options = {}) {
  const XHR = options.XHR ?? globalThis.XMLHttpRequest;
  const Form = options.FormData ?? globalThis.FormData;
  const { signal, onProgress } = options;
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(cancelled());
      return;
    }
    const xhr = new XHR();
    xhr.open("POST", url);
    xhr.upload.onprogress = (/** @type {ProgressEvent} */ event) => {
      if (event.lengthComputable && event.total) onProgress?.(event.loaded / event.total);
    };
    xhr.onload = () => {
      let body = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        // Not JSON, such as a proxy's error page.
      }
      resolve({ status: xhr.status, statusText: xhr.statusText, body });
    };
    xhr.onerror = () =>
      reject(new TypeError("The upload was cut off. Check the connection and try again."));
    xhr.onabort = () => reject(cancelled());
    signal?.addEventListener("abort", () => xhr.abort(), { once: true });
    const form = new Form();
    form.append("file", file);
    xhr.send(form);
  });
}

/** @param {unknown} error */
export const isCancelled = (error) => error instanceof Error && error.name === "AbortError";
