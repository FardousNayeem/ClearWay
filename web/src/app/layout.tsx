import type { Metadata, Viewport } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";

import "./globals.css";

const sans = Geist({ subsets: ["latin"], variable: "--font-geist-sans", display: "swap" });
const mono = Geist_Mono({ subsets: ["latin"], variable: "--font-geist-mono", display: "swap" });

export const metadata: Metadata = {
  metadataBase: new URL("https://clearway.example"),
  title: {
    default: "ClearWay - air quality you can act on",
    template: "%s | ClearWay",
  },
  description:
    "Hourly PM2.5 nowcast and 24-hour forecast, bias-corrected from the CAMS physics model and scored every day against what actually happened.",
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f4f6f7" },
    { media: "(prefers-color-scheme: dark)", color: "#0c1013" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className={`${sans.variable} ${mono.variable} font-sans antialiased`}>
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-accent focus:px-4 focus:py-2 focus:text-on-accent"
        >
          Skip to content
        </a>

        <div className="flex min-h-[100dvh] flex-col">
          <header className="border-b border-line bg-surface">
            <div className="mx-auto flex h-16 max-w-[1180px] items-center justify-between gap-6 px-4 sm:px-6">
              <Link href="/" className="flex items-center gap-2">
                <svg viewBox="0 0 24 24" className="size-[22px]" aria-hidden>
                  <rect width="24" height="24" rx="6" fill="var(--ink)" />
                  <path
                    d="M5 14.5c2.6 0 2.6-3 5.2-3s2.6 3 5.2 3M5 10c2.6 0 2.6-3 5.2-3s2.6 3 5.2 3"
                    stroke="var(--accent)"
                    strokeWidth="1.7"
                    strokeLinecap="round"
                    fill="none"
                  />
                  <circle cx="18.5" cy="16.5" r="1.6" fill="var(--accent)" />
                </svg>
                <span className="text-[17px] font-semibold tracking-tight text-ink">
                  ClearWay
                </span>
              </Link>

              <nav className="flex items-center gap-1" aria-label="Main">
                <Link
                  href="/"
                  className="rounded-sm px-3 py-2 text-[13.5px] text-muted transition-colors hover:bg-sunk hover:text-ink"
                >
                  Air quality
                </Link>
                <Link
                  href="/model"
                  className="rounded-sm px-3 py-2 text-[13.5px] text-muted transition-colors hover:bg-sunk hover:text-ink"
                >
                  How accurate is it
                </Link>
              </nav>
            </div>
          </header>

          <main id="main" className="flex-1">
            {children}
          </main>

          <footer className="border-t border-line bg-surface">
            <div className="mx-auto grid max-w-[1180px] gap-2 px-4 py-6 text-[12.5px] text-muted sm:px-6">
              <p>
                Weather and air quality data by{" "}
                <a
                  href="https://open-meteo.com/"
                  className="text-accent hover:underline"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Open-Meteo
                </a>{" "}
                under CC BY 4.0, using the Copernicus CAMS forecast. Ground truth from{" "}
                <a
                  href="https://openaq.org/"
                  className="text-accent hover:underline"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  OpenAQ
                </a>{" "}
                and the{" "}
                <a
                  href="https://aqicn.org/"
                  className="text-accent hover:underline"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  World Air Quality Index
                </a>{" "}
                project. Basemap by OpenFreeMap and OpenStreetMap contributors.
              </p>
              <p>
                ClearWay is a experimental project. It is not an official air quality
                advisory, and it should not be used for medical decisions.
              </p>
            </div>
          </footer>
        </div>
      </body>
    </html>
  );
}
