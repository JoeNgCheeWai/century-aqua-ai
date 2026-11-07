import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Century Aquaculture | Intelligence for Modern Aquaculture",
  description: "Century Aquaculture combines premium aquaculture products and services with an advanced AI intelligence platform.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body
        className="antialiased"
        style={{
          fontFamily: 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
        }}
      >
        {children}
      </body>
    </html>
  );
}