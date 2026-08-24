"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { ShieldCheck, Lock, User, AlertTriangle } from "lucide-react";
import { login } from "@/lib/auth";

export default function LoginPage() {
  const router = useRouter();
  const [operatorId, setOperatorId] = useState("");
  const [passcode, setPasscode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmitting(true);
    setError(null);

    // Tiny artificial delay so the form doesn't feel like it's not doing
    // anything real — this is still a synchronous client-side check.
    window.setTimeout(() => {
      const session = login(operatorId, passcode);
      if (!session) {
        setError("Operator ID or passcode not recognized.");
        setSubmitting(false);
        return;
      }
      router.replace("/live");
    }, 250);
  };

  return (
    <div className="dot-grid-bg flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center text-center">
          <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-lg border border-safety-500/40 bg-safety-500/10">
            <ShieldCheck className="h-6 w-6 text-safety-500" aria-hidden="true" />
          </div>
          <h1 className="font-headline text-xl font-bold tracking-tight2 text-ink">
            PRAHARI
          </h1>
          <p className="mt-1 text-[11px] uppercase tracking-wide2 text-ink-dim">
            Border Security Hub — Operator Access
          </p>
        </div>

        <form
          onSubmit={handleSubmit}
          className="rounded-lg border border-obsidian-border bg-obsidian-900/60 p-6"
          aria-describedby={error ? "login-error" : undefined}
        >
          <div className="mb-4">
            <label htmlFor="operatorId" className="mb-1.5 block text-xs font-medium text-ink-muted">
              Operator ID
            </label>
            <div className="relative">
              <User
                className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-dim"
                aria-hidden="true"
              />
              <input
                id="operatorId"
                type="text"
                autoComplete="username"
                required
                value={operatorId}
                onChange={(e) => setOperatorId(e.target.value)}
                placeholder="OP-774"
                className="w-full rounded-md border border-obsidian-border bg-obsidian-950 py-2 pl-9 pr-3 text-sm text-ink placeholder:text-ink-dim focus:border-safety-500 focus:outline-none"
              />
            </div>
          </div>

          <div className="mb-5">
            <label htmlFor="passcode" className="mb-1.5 block text-xs font-medium text-ink-muted">
              Passcode
            </label>
            <div className="relative">
              <Lock
                className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-dim"
                aria-hidden="true"
              />
              <input
                id="passcode"
                type="password"
                autoComplete="current-password"
                required
                value={passcode}
                onChange={(e) => setPasscode(e.target.value)}
                placeholder="••••••••"
                className="w-full rounded-md border border-obsidian-border bg-obsidian-950 py-2 pl-9 pr-3 text-sm text-ink placeholder:text-ink-dim focus:border-safety-500 focus:outline-none"
              />
            </div>
          </div>

          {error && (
            <div
              id="login-error"
              role="alert"
              className="mb-4 flex items-start gap-2 rounded-md border border-critical/40 bg-critical/10 px-3 py-2 text-xs text-critical"
            >
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              <span>{error}</span>
            </div>
          )}

          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded-md bg-safety-500 py-2 text-sm font-bold uppercase tracking-wide2 text-obsidian-950 transition-opacity hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-safety-500 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {submitting ? "Verifying..." : "Sign In"}
          </button>
        </form>

        <p className="mt-4 text-center text-[10px] text-ink-dim">
          Restricted system. Authorized camera operators only.
        </p>
      </div>
    </div>
  );
}
