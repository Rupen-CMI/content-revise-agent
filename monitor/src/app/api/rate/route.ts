import { NextRequest, NextResponse } from "next/server";
import { requireAdminApi } from "@/lib/auth";
import { getUsdInrRate } from "@/lib/rate";

// The header's rate badge polls this so the number stays current without a page reload. The browser still
// never contacts a rate provider: the server does, and getUsdInrRate() caches it.
export async function GET(req: NextRequest) {
  const blocked = await requireAdminApi(req);
  if (blocked) return blocked;
  const info = await getUsdInrRate();
  return NextResponse.json({ info }, { headers: { "cache-control": "no-store" } });
}
