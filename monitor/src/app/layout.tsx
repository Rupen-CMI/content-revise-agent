import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "CMI Blog Agent — Usage",
  description: "Usage monitoring dashboard for the CMI Blog Revamp Agent",
  robots: { index: false, follow: false },
};

// Runs before first paint so there is no light->dark flash. Same rule as the agent's own frontend:
// the saved choice wins, otherwise follow the OS preference.
const THEME_INIT = `try{var t=localStorage.getItem("cmi-revamp-theme");if(!t){t=window.matchMedia&&window.matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light"}document.documentElement.dataset.theme=t}catch(e){document.documentElement.dataset.theme="light"}`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" data-theme="light" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
