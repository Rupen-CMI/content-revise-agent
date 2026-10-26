import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { requireAdminApi, sameOriginOnly } from "@/lib/auth";
import { createToken, listTokens, revokeToken } from "@/lib/tokens";

export const runtime = "nodejs";

// Admin API. Gated three ways: middleware.ts (session cookie), requireAdminApi (re-checked here so a
// middleware bypass can't expose it) and, for mutations, sameOriginOnly (CSRF). Responses are no-store:
// the create response contains a live secret.
const NO_STORE = { "cache-control": "no-store" };
const json = (body: unknown, status = 200) => NextResponse.json(body, { status, headers: NO_STORE });

export async function GET(req: NextRequest) {
  const denied = await requireAdminApi(req);
  if (denied) return denied;
  return json(await listTokens());
}

const createSchema = z.object({
  label: z.string().trim().min(1).max(120),
  daily_budget_usd: z.number().positive().max(10_000).default(5),
});

/** Returns the token ONCE; only its hash is stored. */
export async function POST(req: NextRequest) {
  const denied = (await requireAdminApi(req)) ?? sameOriginOnly(req);
  if (denied) return denied;
  const parsed = createSchema.safeParse(await req.json().catch(() => null));
  if (!parsed.success) return json({ error: "invalid body" }, 400);
  const { id, token } = await createToken(parsed.data.label, parsed.data.daily_budget_usd);
  return json({ id, token, label: parsed.data.label }, 201);
}

export async function DELETE(req: NextRequest) {
  const denied = (await requireAdminApi(req)) ?? sameOriginOnly(req);
  if (denied) return denied;
  const id = req.nextUrl.searchParams.get("id") ?? "";
  if (!/^[0-9a-f-]{36}$/i.test(id)) return json({ error: "invalid id" }, 400);
  const revoked = await revokeToken(id);
  return json({ revoked }, revoked ? 200 : 404);
}
