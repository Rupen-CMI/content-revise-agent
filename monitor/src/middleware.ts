import { NextRequest, NextResponse } from "next/server";
import {
  renewSessionToken,
  SESSION_COOKIE,
  sessionCookieOptions,
  verifySessionToken,
} from "@/lib/session";

// Public (not session-protected) paths, matched EXACTLY so a lookalike such as /loginx or /api/ingestx
// is never accidentally public:
//   /login            the login page (its server action posts here)
//   /api/ingest       the agent's usage events (own auth: per-install token or INGEST_KEY)
//   /api/v1/*         the LLM proxy (own auth: per-install bearer token)
// Everything else needs the admin session. Pages and the admin API re-check the session themselves
// (src/lib/auth.ts), so this is defence in depth, not the only gate.
export function isPublicPath(pathname: string): boolean {
  return pathname === "/login" || pathname === "/api/ingest" || pathname.startsWith("/api/v1/");
}

// Next's <Link> prefetches routes the user merely has in view. That is the browser guessing, not the
// admin acting, so it must not keep an idle session alive.
function isPrefetch(req: NextRequest): boolean {
  return (
    req.headers.has("next-router-prefetch") ||
    /prefetch/i.test(req.headers.get("purpose") ?? "") ||
    /prefetch/i.test(req.headers.get("sec-purpose") ?? "")
  );
}

export async function middleware(req: NextRequest) {
  const path = req.nextUrl.pathname;
  if (isPublicPath(path)) return NextResponse.next();

  const secret = process.env.SESSION_SECRET ?? "";
  const token = req.cookies.get(SESSION_COOKIE)?.value;
  if (await verifySessionToken(token, secret)) {
    const res = NextResponse.next();
    // Sliding idle timeout: any real request from a signed-in admin pushes the idle deadline out. The
    // renewed cookie rides on this response; the page/route below still sees (and re-checks) the old one.
    if (path !== "/logout" && !isPrefetch(req)) {
      const renewed = await renewSessionToken(token, secret);
      if (renewed) res.cookies.set(SESSION_COOKIE, renewed.value, sessionCookieOptions(renewed.maxAge));
    }
    return res;
  }

  if (path.startsWith("/api/")) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  // a cookie that is present but no longer valid means it timed out; say so on the login page
  const login = new URL("/login", req.url);
  if (token) login.searchParams.set("reason", "expired");
  return NextResponse.redirect(login);
}

export const config = {
  // only static assets are skipped here (the favicon must load on the login page too); everything else
  // goes through middleware()
  matcher: ["/((?!_next/static|_next/image|favicon\\.ico|icon\\.svg$).*)"],
};
