export type EngineTab = "summary" | "statistical" | "ml" | "vintages";
export function ForecastEnginesTabs({ value, onChange }: { value: EngineTab; onChange: (tab: EngineTab) => void }) {
  const tabs = [["summary", "Resumen"], ["statistical", "Motor Estadístico"], ["ml", "Machine Learning"], ["vintages", "Vintages"]] as const;
  return <div role="tablist" aria-label="Detalle de motores" className="flex flex-wrap gap-2 border-b border-slate-200 pb-3">{tabs.map(([id, label], index) => <button type="button" key={id} id={`engine-tab-${id}`} role="tab" tabIndex={value === id ? 0 : -1} aria-selected={value === id} aria-controls={`engine-panel-${id}`} onClick={() => onChange(id)} onKeyDown={event => {
    if (!["ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length;
    onChange(tabs[next][0]);
    (event.currentTarget.parentElement?.children[next] as HTMLButtonElement | undefined)?.focus();
  }} className={`rounded-lg px-4 py-2 text-sm font-medium ${value === id ? "bg-blue-50 text-blue-800" : "text-slate-500 hover:bg-slate-50"}`}>{label}</button>)}</div>;
}
