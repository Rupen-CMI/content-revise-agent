/** Message safe to show on a page. Raw driver errors can contain hostnames, role names or SQL, so in
 * production the details go to the server log only; in development you still see the real error. */
export function publicError(e: unknown, fallback = "Could not reach the database. Check DATABASE_URL and the Neon project."): string {
  const detail = e instanceof Error ? e.message : String(e);
  if (process.env.NODE_ENV !== "production") return detail;
  console.error("[dashboard] query failed:", e);
  return fallback;
}
