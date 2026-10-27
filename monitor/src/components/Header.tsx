import Link from "next/link";
import { demoEnabled } from "@/lib/demo";
import { SESSION_IDLE_S } from "@/lib/session";
import { BrandMark } from "./BrandMark";
import { IdleGuard } from "./IdleGuard";
import { ThemeToggle } from "./ThemeToggle";

export function Header({ active }: { active: "usage" | "tokens" }) {
  return (
    <header className="header">
      <IdleGuard idleSeconds={SESSION_IDLE_S} />
      <Link href="/" className="brand">
        <BrandMark />
        <span>CMI Blog Agent</span>
      </Link>
      <nav className="nav">
        <Link href="/" aria-current={active === "usage" ? "page" : undefined}>Usage</Link>
        <Link href="/tokens" aria-current={active === "tokens" ? "page" : undefined}>LLM tokens</Link>
      </nav>
      <div className="header-right">
        {demoEnabled() ? <span className="demo-pill">demo data</span> : null}
        <ThemeToggle />
        <form action="/logout" method="post">
          <button type="submit" className="signout">Sign out</button>
        </form>
      </div>
    </header>
  );
}
