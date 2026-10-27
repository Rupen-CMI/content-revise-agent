import { NextRequest, NextResponse } from "next/server";
import { ingestSchema, insertEvent, locationFromHeaders, MIN_NAME_LENGTH, upsertInstallName } from "@/lib/events";
import { secretsMatch } from "@/lib/session";
import { lookupToken } from "@/lib/tokens";

export const runtime = "nodejs";

const NO_STORE = { "cache-control": "no-store" };
// A real event is well under 1 KB; anything near this size is not one.
const MAX_BODY_BYTES = 16 * 1024;

const json = (body: unknown, status: number) => NextResponse.json(body, { status, headers: NO_STORE });

/**
 * POST /api/ingest — the agent (backend/telemetry.py) posts usage events here.
 *
 * Auth, either of:
 *   - Authorization: Bearer cmi_...   the install's own proxy token (preferred). The user label is then
 *     taken from the TOKEN, never from the payload, so one install cannot report under another's name,
 *     and revoking the token cuts the install off. No shared secret ships in the package.
 *   - X-Ingest-Key: <INGEST_KEY>      legacy shared key for --direct builds (constant-time compare).
 * Idempotent on event_id (INSERT ... ON CONFLICT DO NOTHING).
 */
export async function POST(req: NextRequest) {
  let label: string | null = null;

  const auth = req.headers.get("authorization") ?? "";
  const bearer = auth.toLowerCase().startsWith("bearer ") ? auth.slice(7).trim() : "";
  let authorised = false;
  if (bearer) {
    try {
      const token = await lookupToken(bearer);
      if (token) {
        authorised = true;
        label = token.label;
      }
    } catch (e) {
      console.error("[ingest] token lookup failed:", e);
      return json({ error: "storage failure" }, 500);
    }
  } else {
    authorised = await secretsMatch(req.headers.get("x-ingest-key") ?? "", process.env.INGEST_KEY ?? "");
  }
  if (!authorised) return json({ error: "unauthorized" }, 401);

  const raw = await req.text();
  if (raw.length > MAX_BODY_BYTES) return json({ error: "payload too large" }, 413);
  let body: unknown;
  try {
    body = JSON.parse(raw);
  } catch {
    return json({ error: "invalid JSON" }, 400);
  }

  const parsed = ingestSchema.safeParse(body);
  if (!parsed.success) {
    return json(
      {
        error: "invalid event",
        issues: parsed.error.issues.slice(0, 10).map((i) => ({ path: i.path.join("."), message: i.message })),
      },
      400,
    );
  }

  const event = label === null ? parsed.data : { ...parsed.data, user_label: label };
  try {
    if (event.type === "name_updated") {
      // not an `events` row: it only records the install's display name (current + previous)
      if (event.user_name.length < MIN_NAME_LENGTH) return json({ error: "user_name required" }, 400);
      await upsertInstallName(event.install_id, event.user_name, event.ts);
      return json({ ok: true }, 202);
    }
    const inserted = await insertEvent(event, locationFromHeaders(req.headers));
    // every event carries the user's current name; applied only if newer than what is stored
    await upsertInstallName(event.install_id, event.user_name, event.ts);
    return json({ ok: true, deduped: !inserted }, 202);
  } catch (e) {
    console.error("[ingest] insert failed:", e);
    return json({ error: "storage failure" }, 500);
  }
}
