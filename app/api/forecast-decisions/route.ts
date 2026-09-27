import { disabledPortalOperation } from "@/lib/portal-operation-gate";
export async function POST() { return disabledPortalOperation(); }
export async function GET() { return disabledPortalOperation(); }
