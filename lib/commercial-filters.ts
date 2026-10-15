import type { CommercialClassification, Filters, ForecastReadRepository, Product } from "./supabase/types";

export const unknownCommercial = "__UNKNOWN__";
export const notApplicableCommercial = "__NOT_APPLICABLE__";
export const commercialFields = { commercialType: "product_type", commercialModels: "model", commercialColor: "color", commercialSize: "size" } as const;
export type CommercialFilter = keyof typeof commercialFields;
// Comparison/display only. NFC preserves accents and compatibility characters;
// no accent removal, token deletion or fuzzy matching is authorized.
export function modelKey(value: string) {
  return value.normalize("NFC").trim().replace(/\s+/gu, " ").toLocaleUpperCase("es-MX").normalize("NFC");
}
function attributeValue(p: Product, field: CommercialFilter) {
  const attribute = commercialFields[field];
  const value = p.commercial?.[attribute];
  return value ? field === "commercialModels" ? modelKey(value) || unknownCommercial : value : (p.commercial?.not_applicable?.some(v => v === attribute) ? notApplicableCommercial : unknownCommercial);
}
export function hasCommercialFilters(f: Filters) {
  return Boolean(f.classificationStatus || Object.keys(commercialFields).some(k => f[k as CommercialFilter]));
}
export function matchesCommercial(p: Product, filters: Filters, except?: CommercialFilter) {
  if (filters.classificationStatus && (p.commercial?.status ?? "PENDING_CLASSIFICATION") !== filters.classificationStatus) return false;
  return (Object.keys(commercialFields) as CommercialFilter[]).every(filter => {
    const value = filters[filter];
    return filter === except || value == null || (Array.isArray(value) ? value.map(modelKey).includes(attributeValue(p, filter)) : attributeValue(p, filter) === value);
  });
}
export function attachCommercial(products: Product[], classifications: CommercialClassification[]) {
  const map = new Map<string, CommercialClassification>();
  for (const row of classifications) {
    if (map.has(row.product_id)) throw new Error("commercial_duplicate_product");
    map.set(row.product_id, row);
  }
  return products.map(p => {
    const commercial = map.get(p.id);
    if (!commercial || commercial.chain_id !== p.chain_id) throw new Error("commercial_scope_mismatch");
    return { ...p, commercial };
  });
}
export async function filteredProducts(repository: ForecastReadRepository, filters: Filters) {
  let products = await repository.getProducts(filters.chainId, filters.categoryId, filters.search);
  if (repository.getCommercialCatalog && hasCommercialFilters(filters)) products = attachCommercial(products, await repository.getCommercialCatalog());
  else if (hasCommercialFilters(filters)) throw new Error("commercial_catalog_unavailable");
  return products.filter(p => (!filters.productId || p.id === filters.productId) && matchesCommercial(p, filters));
}
export function commercialOptions(products: Product[], filters: Filters, field: CommercialFilter) {
  const options = new Map<string, { id: string; name: string; count: number; pending: boolean; statuses: string[] }>();
  const scope = field === "commercialModels" ? { ...filters, commercialColor: null, commercialSize: null } : filters;
  for (const p of products.filter(p => matchesCommercial(p, scope, field))) {
    const value = attributeValue(p, field);
    const option = options.get(value) ?? { id: value, name: value === unknownCommercial ? "Sin identificar" : value === notApplicableCommercial ? "No aplica (documentado)" : value, count: 0, pending: false, statuses: [] };
    option.count++; option.pending ||= p.commercial?.status !== "APPROVED";
    const status = p.commercial?.status ?? "PENDING_CLASSIFICATION";
    if (!option.statuses.includes(status)) option.statuses.push(status);
    options.set(value, option);
  }
  return [...options.values()].sort((a, b) => a.name.localeCompare(b.name, "es"));
}
export function changeCommercial(filters: Filters, field: CommercialFilter, value: string | string[] | null): Filters {
  if (field === "commercialModels" && value !== null && !Array.isArray(value) || field !== "commercialModels" && Array.isArray(value)) throw new Error("invalid_commercial_selection");
  // A parent facet change never retains an incompatible downstream identity.
  const fields = Object.keys(commercialFields) as CommercialFilter[];
  return { ...filters, ...Object.fromEntries(fields.slice(fields.indexOf(field) + 1).map(f => [f, null])), [field]: Array.isArray(value) ? [...new Set(value.map(modelKey))] : value, productId: null };
}
