"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function RootPage() {
  const router = useRouter();

  useEffect(() => {
    router.replace("/live");
  }, [router]);

  return (
    <div className="flex h-screen items-center justify-center bg-obsidian-950 text-xs font-mono text-ink-dim">
      Redirecting to Live Feed...
    </div>
  );
}
