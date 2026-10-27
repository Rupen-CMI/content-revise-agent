import Link from "next/link";
import { demoEnabled } from "@/lib/demo";
import { getMoney } from "@/lib/money";
import { describeRate } from "@/lib/rate";
import { SESSION_IDLE_S } from "@/lib/session";
import { BrandMark } from "./BrandMark";
import { CurrencyToggle } from "./CurrencyToggle";
import { IdleGuard } from "./IdleGuard";
import { ThemeToggle } from "./ThemeToggle";

export async function Header({ active }: { active: "usage" | "tokens" }) {
  const { money, info } = await getMoney();
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
        <CurrencyToggle
          currency={money.currency}
          inrAvailable={info !== null}
          rateLabel={info ? describeRate(info) : "Exchange rate unavailable right now"}
        />
        <ThemeToggle />
        <form action="/logout" method="post">
          <button type="submit" className="signout">Sign out</button>
        </form>
      </div>
    </header>
  );
}
