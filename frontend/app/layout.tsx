import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Nunes Recruitment Console",
  description: "Recruitment operations console",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
