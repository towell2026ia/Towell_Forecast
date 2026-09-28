import type { Metadata } from "next";
import UpdatePassword from "./update-password";
import { publicPortalConfig } from "@/lib/supabase/public-config";
export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Restablecer contraseña · FORECAST Towell", referrer: "no-referrer" };
export default function UpdatePasswordPage() {
  return <UpdatePassword config={publicPortalConfig()}/>;
}
