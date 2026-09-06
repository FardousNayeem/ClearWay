import type { ReactNode } from "react";

import { cn } from "@/lib/format";

export function Panel({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("rounded-lg border border-line bg-surface", className)}>
      {children}
    </section>
  );
}

export function PanelHeader({
  title,
  description,
  action,
}: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-5 py-4">
      <div>
        <h2 className="text-[15px] font-semibold tracking-tight text-ink">{title}</h2>
        {description && <p className="mt-0.5 text-[13px] text-muted">{description}</p>}
      </div>
      {action}
    </header>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("skeleton", className)} aria-hidden />;
}

export function SkeletonPanel({ rows = 3 }: { rows?: number }) {
  return (
    <div className="grid gap-2 p-5" aria-busy="true">
      {Array.from({ length: rows }, (_, index) => (
        <Skeleton key={index} className="h-12 w-full rounded-md" />
      ))}
      <span className="sr-only">Loading</span>
    </div>
  );
}

export function EmptyState({ title, description }: { title: string; description: string }) {
  return (
    <div className="grid justify-items-center gap-2 px-6 py-12 text-center">
      <h3 className="text-[15px] font-semibold text-ink">{title}</h3>
      <p className="max-w-[46ch] text-[13.5px] leading-relaxed text-muted">{description}</p>
    </div>
  );
}

export function Alert({
  tone = "warn",
  children,
}: {
  tone?: "warn" | "info";
  children: ReactNode;
}) {
  return (
    <div
      role="status"
      className={cn(
        "rounded-md border px-4 py-3 text-[13.5px]",
        tone === "warn"
          ? "border-line-strong bg-sunk text-ink-soft"
          : "border-accent/30 bg-accent-quiet text-ink-soft",
      )}
    >
      {children}
    </div>
  );
}

export function Chip({
  active,
  children,
  ...props
}: {
  active?: boolean;
  children: ReactNode;
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      aria-pressed={active}
      className={cn(
        "rounded-sm border px-2.5 py-1.5 text-[12.5px] transition-colors",
        active
          ? "border-accent bg-accent text-on-accent"
          : "border-line-strong bg-surface text-muted hover:text-ink",
      )}
      {...props}
    >
      {children}
    </button>
  );
}
