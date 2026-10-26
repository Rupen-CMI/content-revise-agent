// `npm run demo` -- run the REAL dashboard on http://localhost:3100 with built-in sample data and
// no database (login password: demo). Development server only; demo data is hard-disabled in
// production builds (see src/lib/demo.ts).
import { spawn } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const next = join(root, "node_modules", "next", "dist", "bin", "next");
const child = spawn(process.execPath, [next, "dev", "-p", "3100"], {
  cwd: root,
  stdio: "inherit",
  env: {
    ...process.env,
    NODE_ENV: "development",
    MONITOR_DEMO: "1",
    ADMIN_PASSWORD: "demo",
    SESSION_SECRET: "demo-session-secret-not-for-production-0123456789",
    DATABASE_URL: "postgres://demo:demo@localhost/demo",
  },
});
child.on("exit", (code) => process.exit(code ?? 0));
