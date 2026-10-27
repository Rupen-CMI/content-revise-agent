"use server";

import { cookies, headers } from "next/headers";
import { redirect } from "next/navigation";
import {
  createSessionToken,
  SESSION_COOKIE,
  SESSION_IDLE_S,
  secretsMatch,
  sessionCookieOptions,
} from "@/lib/session";
import { clearFailures, clientIp, ipKey, isLockedOut, recordFailure } from "@/lib/throttle";

export interface LoginState {
  error?: string;
}

export async function loginAction(
  _prev: LoginState,
  formData: FormData,
): Promise<LoginState> {
  const password = String(formData.get("password") ?? "");
  const expected = process.env.ADMIN_PASSWORD ?? "";
  const secret = process.env.SESSION_SECRET ?? "";
  if (!secret) {
    return { error: "Server misconfiguration: SESSION_SECRET is not set." };
  }

  const who = ipKey(clientIp(await headers()), secret);
  if (await isLockedOut(who)) {
    return { error: "Too many failed attempts. Try again in 15 minutes." };
  }

  // constant-time: compare SHA-256 digests of both values
  if (!expected || !password || !(await secretsMatch(password, expected))) {
    await recordFailure(who);
    // tiny delay to blunt online guessing; the real limit is the throttle above
    await new Promise((r) => setTimeout(r, 300));
    return { error: "Wrong password." };
  }

  await clearFailures(who);
  const jar = await cookies();
  jar.set(SESSION_COOKIE, await createSessionToken(secret), sessionCookieOptions(SESSION_IDLE_S));
  redirect("/");
}
