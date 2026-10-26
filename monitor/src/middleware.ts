import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, verifySessionToken } from "@/lib/session";

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

export async function middleware(req: NextRequest) {
  if (isPublicPath(req.nextUrl.pathname)) return NextResponse.next();

  const secret = process.env.SESSION_SECRET ?? "";
  const token = req.cookies.get(SESSION_COOKIE)?.value;
  if (await verifySessionToken(token, secret)) return NextResponse.next();

  if (req.nextUrl.pathname.startsWith("/api/")) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  return NextResponse.redirect(new URL("/login", req.url));
}

export const config = {
  // only static assets are skipped here; everything else goes through middleware()
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
