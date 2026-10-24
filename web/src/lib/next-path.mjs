// Plain JavaScript so node --test can run it without a build step.

/**
 * Where to go after logging in: the page from `?next=` when it is on this site, and the
 * jobs page otherwise. Never another site, and never the login page itself.
 * @param {string | null | undefined} next
 * @param {string} origin this site's origin, such as http://192.168.1.2:8000
 */
export function safeNext(next, origin) {
  if (!next || !next.startsWith("/")) return "/";
  let url;
  try {
    url = new URL(next, origin);
  } catch {
    return "/";
  }
  if (url.origin !== origin || url.pathname.startsWith("/login")) return "/";
  return url.pathname + url.search + url.hash;
}

/**
 * The login page, set to come back to `here` afterwards.
 * @param {string} here path and query of the page being left
 */
export function loginPath(here) {
  if (!here.startsWith("/") || here.startsWith("/login")) return "/login/";
  return here === "/" ? "/login/" : `/login/?next=${encodeURIComponent(here)}`;
}
