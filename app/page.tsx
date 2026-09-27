import PortalAuth from "./portal-auth";

export const dynamic = "force-dynamic";

export default async function Home() {
  return <PortalAuth config={{ url: process.env.NEXT_PUBLIC_SUPABASE_URL ?? "", key: process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY ?? process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "" }}/>;
}
