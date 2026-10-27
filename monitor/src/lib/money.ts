import { cookies } from "next/headers";
import { CURRENCY_COOKIE, parseCurrency, USD_MONEY, type Currency, type Money } from "./currency";
import { getUsdInrRate, type RateInfo } from "./rate";

export interface MoneyContext {
  /** what to format with: INR only when it was chosen AND a rate is available, otherwise USD */
  money: Money;
  /** the live rate (also when showing USD, so the toggle knows whether INR is offered); null if unavailable */
  info: RateInfo | null;
  /** what the visitor asked for; differs from money.currency when INR was chosen but no rate could be had */
  requested: Currency;
}

/** For server components: the visitor's display currency, resolved against the cached exchange rate. */
export async function getMoney(): Promise<MoneyContext> {
  const requested = parseCurrency((await cookies()).get(CURRENCY_COOKIE)?.value);
  const info = await getUsdInrRate();
  if (requested === "INR" && info) return { money: { currency: "INR", rate: info.rate }, info, requested };
  return { money: USD_MONEY, info, requested };
}
