import { NextRequest, NextResponse } from "next/server";
import { sameOriginOnly } from "@/lib/auth";
import { SESSION_COOKIE } from "@/lib/session";

// POST only: a GET logout can be triggered by any page (<img src="/logout">). 303 so the browser
// follows with a GET to /login.
export async function POST(req: NextRequest) {
  const blocked = sameOriginOnly(req);
  if (blocked) return blocked;
  const res = NextResponse.redirect(new URL("/login", req.url), 303);
  res.cookies.delete(SESSION_COOKIE);
  return res;
}
