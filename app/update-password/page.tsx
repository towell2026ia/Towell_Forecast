import type { Metadata } from "next";
import UpdatePassword from "./update-password";
export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Restablecer contraseña · FORECAST Towell", referrer: "no-referrer" };
export default function UpdatePasswordPage() {
  return <UpdatePassword config={{ url: process.env.NEXT_PUBLIC_SUPABASE_URL ?? "", key: process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY ?? process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "" }}/>;
}
