import type { MoneyContext } from "@/lib/money";
import { describeRate } from "@/lib/rate";

/** One line under a page title saying which rate the rupee amounts use (nothing in USD mode). Money is
 * always recorded in USD, so the note also says the INR figures are an estimate. */
export function CurrencyNote({ ctx }: { ctx: MoneyContext }) {
  if (ctx.money.currency === "INR" && ctx.info) {
    return (
      <p className="muted currency-note">
        Costs shown in ₹ at {describeRate(ctx.info)}. They are recorded and billed in USD, so treat ₹ as an estimate.
      </p>
    );
  }
  if (ctx.requested === "INR") {
    return <p className="muted currency-note">The ₹ exchange rate is unavailable right now, so costs are shown in USD.</p>;
  }
  return null;
}
