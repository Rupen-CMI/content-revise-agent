import { NextRequest, NextResponse } from "next/server";
import { sameOriginOnly } from "@/lib/auth";
import { SESSION_COOKIE, sessionCookieOptions } from "@/lib/session";

// POST only: a GET logout can be triggered by any page (<img src="/logout">). 303 so the browser
// follows with a GET to /login.
export async function POST(req: NextRequest) {
  const blocked = sameOriginOnly(req);
  if (blocked) return blocked;
  const res = NextResponse.redirect(new URL("/login", req.url), 303);
  // A "__Host-" cookie can only be overwritten by a Set-Cookie that is itself Secure, Path=/ with no
  // Domain. cookies.delete() omits Secure, so browsers silently ignored it and the session survived
  // sign-out. Expire it with the same attributes it was issued with.
  res.cookies.set(SESSION_COOKIE, "", { ...sessionCookieOptions(0), expires: new Date(0) });
  return res;
}
