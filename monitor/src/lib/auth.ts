import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, verifySessionToken } from "./session";

// middleware.ts is the first gate; these re-check the session where the data is actually read, so a
// middleware bypass (misconfigured matcher, a framework-level bug) can never expose data by itself.

/** For server components: send an unauthenticated visitor to the login page. */
export async function requireAdminPage(): Promise<void> {
  const jar = await cookies();
  const ok = await verifySessionToken(jar.get(SESSION_COOKIE)?.value, process.env.SESSION_SECRET);
  if (!ok) redirect("/login");
}

/** For route handlers: null when authorised, otherwise the response to return. */
export async function requireAdminApi(req: NextRequest): Promise<NextResponse | null> {
  const ok = await verifySessionToken(req.cookies.get(SESSION_COOKIE)?.value, process.env.SESSION_SECRET);
  return ok ? null : NextResponse.json({ error: "unauthorized" }, { status: 401 });
}

/** CSRF defence for cookie-authenticated mutations (on top of SameSite=Lax): refuse requests the
 * browser says came from another site. Non-browser clients send neither header and still need the
 * session cookie. */
export function sameOriginOnly(req: NextRequest): NextResponse | null {
  const site = req.headers.get("sec-fetch-site");
  if (site && site !== "same-origin" && site !== "none") {
    return NextResponse.json({ error: "cross-site request refused" }, { status: 403 });
  }
  // Our Referrer-Policy is no-referrer, and with that policy browsers send "Origin: null" even on a
  // same-origin POST (this is what broke Sign out). Sec-Fetch-Site is set by the browser and cannot be
  // forged by a page, so when it says same-origin the Origin header adds nothing and is not consulted.
  if (site === "same-origin") return null;
  const origin = req.headers.get("origin");
  if (origin) {
    const host = req.headers.get("x-forwarded-host") ?? req.headers.get("host") ?? "";
    let originHost = "";
    try {
      originHost = new URL(origin).host;
    } catch {
      /* malformed Origin falls through to the refusal below */
    }
    if (!originHost || originHost !== host) {
      return NextResponse.json({ error: "cross-origin request refused" }, { status: 403 });
    }
  }
  return null;
}
