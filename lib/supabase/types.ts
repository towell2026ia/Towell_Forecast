export type Role = "ADMIN" | "EDITOR" | "VIEWER";
export type Profile = { id: string; full_name: string; global_role: Role; status: "ACTIVE" | "INACTIVE" };
export type PublicSupabaseConfig = { url: string; key: string; siteUrl?: string };
export type Chain = { id: string; code: string; name: string; status: string; has_history: boolean; parentId: string | null; scopeType: string | null };
export type Category = { id: string; chain_id: string; name: string };
export type Product = { id: string; chain_id: string; category_id: string | null; product_code: string; variant_code: string | null; description: string; identifiers: [string | null, string | null][] };
export type Filters = { chainId: string | null; productId: string | null; categoryId: string | null; periodRange: [string | null, string | null]; search: string };
export const emptyFilters: Filters = { chainId: null, productId: null, categoryId: null, periodRange: [null, null], search: "" };
export type Cursor = { chain_id: string; product_id: string; period: string };
export type HistoricalRow = Cursor & { product: Product; chain: string; SALES: number | null; ORDER: number | null; DELIVERY: number | null; availability: string };
export type HistoricalPage = { rows: HistoricalRow[]; next: Cursor | null; observationCount: number };
export type Summary = { scopeCount: number; productCount: number; observationCount: number; latestPeriod: string | null; byScope: { chain: Chain; observations: number }[] };
export interface ForecastReadRepository {
  getForecastHistory?(filters: Filters): Promise<import("../forecast-chart-data").HistoricalMonth[]>;
  getCustomerForecast?(filters: Filters, issuePeriod: string): Promise<import("../forecast-chart-data").CustomerMonth[]>;
  previews?: import("../forecast-preview").PreviewClient;
  getCurrentProfile(): Promise<Profile>;
  getVisibleChains(): Promise<Chain[]>;
  getCategories(chainId?: string | null): Promise<Category[]>;
  getProducts(chainId?: string | null, categoryId?: string | null, search?: string): Promise<Product[]>;
  getPeriods(chainId?: string | null): Promise<string[]>;
  getHistoricalObservations(filters: Filters, pagination: { size: 50 | 100 | 250; cursor?: Cursor | null }): Promise<HistoricalPage>;
  getHistoricalSummary(filters: Filters): Promise<Summary>;
  getProfiles(): Promise<Profile[]>;
  dispose(): void;
}
