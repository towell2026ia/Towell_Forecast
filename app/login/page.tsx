import PortalAuth from "../portal-auth";
import { publicPortalConfig } from "@/lib/supabase/public-config";
export const dynamic = "force-dynamic";
export default function Login() {
  return <PortalAuth login config={publicPortalConfig()}/>;
}
