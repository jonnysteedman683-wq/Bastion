import { Bot, LayoutDashboard, ScrollText } from "lucide-react";
import { cn } from "@/lib/utils";
type View = "operations" | "roster" | "ledger";

const NAV: { id: View; label: string; icon: typeof LayoutDashboard }[] = [
  { id: "operations", label: "Operations", icon: LayoutDashboard },
  { id: "roster", label: "Roster", icon: Bot },
  { id: "ledger", label: "Ledger", icon: ScrollText },
];

export function AppSidebar({ view, setView }: { view: View; setView: (v: View) => void }) {
  return (
    <aside className="flex h-svh w-64 shrink-0 flex-col border-r bg-card">
      <div className="flex items-center gap-2 px-4 py-4">
        <span className="font-semibold tracking-tight">Bastion</span>
      </div>
      <nav aria-label="Main" className="flex flex-col gap-1 px-2">
        {NAV.map(({ id, label, icon: Icon }) => (
          <button key={id} type="button" onClick={() => setView(id)} aria-current={view === id ? "page" : undefined}
            className={cn("flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground", view === id && "bg-accent font-medium text-accent-foreground")}>
            <Icon className="size-4" aria-hidden /> {label}
          </button>
        ))}
      </nav>
    </aside>
  );
}