import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "FORECAST Towell",
  description: "Consulta del histórico certificado multi-cadena de FORECAST Towell.",
  icons: { icon: "/favicon.svg", shortcut: "/favicon.svg" },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="es"><body>{children}</body></html>;
}
