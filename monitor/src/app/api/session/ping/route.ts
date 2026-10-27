import { NextRequest, NextResponse } from "next/server";
import { requireAdminApi, sameOriginOnly } from "@/lib/auth";

// Keep-alive for IdleGuard. The work happens in middleware.ts, which renews the session cookie on any
// authenticated request; this route only exists so the browser has a cheap request to make while the
// admin is moving the mouse or typing on a page that otherwise makes no requests.
export async function POST(req: NextRequest) {
  const blocked = sameOriginOnly(req) ?? (await requireAdminApi(req));
  if (blocked) return blocked;
  return new NextResponse(null, { status: 204, headers: { "cache-control": "no-store" } });
}
