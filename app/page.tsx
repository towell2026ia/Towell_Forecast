import PortalAuth from "./portal-auth";
import { publicPortalConfig } from "@/lib/supabase/public-config";

export const dynamic = "force-dynamic";

export default async function Home() {
  return <PortalAuth config={publicPortalConfig()}/>;
}
