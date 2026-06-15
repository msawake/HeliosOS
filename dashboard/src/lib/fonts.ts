import { EB_Garamond, Geist, Geist_Mono } from "next/font/google";

// ForgeOS type system — three voices:
//   display: editorial serif, the engraved letterpress voice.
//            Page H1s + the brand wordmark, never body.
//   sans:    grotesque UI workhorse for everything else.
//   mono:    agent ids, costs, tokens, logs, manifests. Pair with tabular-nums.
// Exposed as CSS variables and consumed in globals.css @theme inline.

export const fontDisplay = EB_Garamond({
  subsets: ["latin"],
  weight: ["500", "600", "700"],
  style: ["normal", "italic"],
  variable: "--font-display-next",
  display: "swap",
});

export const fontSans = Geist({
  subsets: ["latin"],
  variable: "--font-sans-next",
  display: "swap",
});

export const fontMono = Geist_Mono({
  subsets: ["latin"],
  variable: "--font-mono-next",
  display: "swap",
});

export const fontVariables = `${fontDisplay.variable} ${fontSans.variable} ${fontMono.variable}`;
