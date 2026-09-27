import PortalAuth from "../portal-auth";
export const dynamic = "force-dynamic";
export default function Login() {
  return <PortalAuth login config={{ url: process.env.NEXT_PUBLIC_SUPABASE_URL ?? "", key: process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY ?? process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "" }}/>;
}
