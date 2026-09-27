import { disabledPortalOperation } from "@/lib/portal-operation-gate";
export async function POST() { return disabledPortalOperation(); }
