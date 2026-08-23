"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { Sidebar } from "@/components/sidebar";
import { getSession, type OperatorSession } from "@/lib/auth";

const PUBLIC_ROUTES = new Set(["/login"]);

/**
 * Client-side gate: the /login page renders full-screen with no sidebar;
 * every other route requires a session in localStorage (see lib/auth.ts)
 * or it redirects to /login. This is a demo-grade convenience gate, not a
 * real security boundary — see the warning in lib/auth.ts.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const isPublicRoute = pathname ? PUBLIC_ROUTES.has(pathname) : false;

  const [session, setSession] = useState<OperatorSession | null>(null);
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    if (isPublicRoute) {
      setChecked(true);
      return;
    }
    const current = getSession();
    setSession(current);
    setChecked(true);
    if (!current) {
      router.replace("/login");
    }
  }, [isPublicRoute, pathname, router]);

  if (isPublicRoute) {
    return <>{children}</>;
  }

  if (!checked || !session) {
    // Avoid flashing protected content while the localStorage check (or
    // the redirect it triggers) is in flight.
    return <div className="min-h-screen bg-obsidian-950" aria-hidden="true" />;
  }

  return (
    <div className="flex min-h-screen">
      <Sidebar operatorId={session.operatorId} />
      <main className="flex-1 min-w-0">{children}</main>
    </div>
  );
}
