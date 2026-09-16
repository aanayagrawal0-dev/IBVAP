"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Video, LayoutGrid, History, BarChart3, ShieldCheck, LogOut, MapPin, ListChecks, Route, ClipboardList,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { logout, hasRole, type OperatorSession, type Role } from "@/lib/auth";

type NavItem = { href: string; label: string; icon: LucideIcon; minRole?: Role };

// minRole gates a nav item; undefined = visible to everyone signed in.
const NAV_ITEMS: NavItem[] = [
  { href: "/live", label: "Live Feed", icon: Video },
  { href: "/registry", label: "Registry", icon: MapPin },
  { href: "/watchlist", label: "Watchlist", icon: ListChecks },
  { href: "/route", label: "Route", icon: Route },
  { href: "/zone-config", label: "Zone Config", icon: LayoutGrid },
  { href: "/history", label: "History", icon: History },
  { href: "/audit", label: "Audit Log", icon: ClipboardList, minRole: "operator" as const },
  { href: "/analytics", label: "Analytics", icon: BarChart3 },
];

const ROLE_LABEL: Record<string, string> = { viewer: "Viewer", operator: "Operator", admin: "Administrator" };

function initials(name: string): string {
  const parts = name.replace(/[^A-Za-z ]/g, "").trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "OP";
  return (parts[0][0] + (parts[1]?.[0] ?? "")).toUpperCase();
}

export function Sidebar({ session }: { session: OperatorSession | null }) {
  const pathname = usePathname();
  const router = useRouter();

  const handleLogout = async () => {
    await logout();
    router.replace("/login");
  };

  const items = NAV_ITEMS.filter((it) => !it.minRole || hasRole(session, it.minRole));

  return (
    <aside
      className="w-60 shrink-0 border-r border-obsidian-border bg-obsidian-950/95 backdrop-blur-sm flex flex-col justify-between"
      aria-label="Primary"
    >
      <div>
        <div className="px-5 py-6 border-b border-obsidian-border">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-5 w-5 text-safety-500" aria-hidden="true" />
            <span className="font-headline text-lg font-bold tracking-tight2 text-ink">PRAHARI</span>
          </div>
          <p className="mt-1 text-[11px] uppercase tracking-wide2 text-ink-dim">GSP CCTV Command</p>
        </div>

        <nav className="px-3 py-4 flex flex-col gap-1">
          {items.map((item) => {
            const active = pathname?.startsWith(item.href);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                  "focus-visible:outline-none",
                  active
                    ? "bg-safety-500/10 text-safety-500 border-l-2 border-safety-500 pl-[10px]"
                    : "text-ink-muted hover:text-ink hover:bg-obsidian-800"
                )}
              >
                <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
                <span className="uppercase tracking-wide2 text-xs">{item.label}</span>
              </Link>
            );
          })}
        </nav>
      </div>

      <div className="px-4 py-4 border-t border-obsidian-border flex items-center gap-3">
        <div
          className="h-9 w-9 rounded-full bg-obsidian-700 flex items-center justify-center text-xs font-mono text-ink-muted"
          aria-hidden="true"
        >
          {initials(session?.displayName || session?.username || "OP")}
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-xs font-medium text-ink truncate">{session?.displayName || session?.username || "—"}</p>
          <p className="text-[10px] uppercase tracking-wide2 text-safety-500">
            {session ? `${ROLE_LABEL[session.role] ?? session.role}${session.department && session.department !== "*" ? ` · ${session.department}` : ""}` : ""}
          </p>
        </div>
        <button
          type="button"
          onClick={handleLogout}
          aria-label="Log out"
          title="Log out"
          className="shrink-0 rounded-md p-1.5 text-ink-muted hover:bg-obsidian-800 hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-safety-500"
        >
          <LogOut className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
    </aside>
  );
}
