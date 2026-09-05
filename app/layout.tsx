import type { Metadata } from "next"
import "./globals.css"

export const metadata: Metadata = {
  title: "Kriniback — Gestion de flotte",
  description: "Console de gestion pour agences de location automobile",
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="fr"><body>{children}</body></html>
}
