"use client";

import Link from "next/link";
import type { ComponentProps, ReactNode } from "react";
import { avatarColors, initial } from "@/lib/format";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

type Variant = "primary" | "secondary" | "ghost" | "danger";

const VARIANT: Record<Variant, string> = {
  primary: "bg-accent text-white hover:bg-accent-hover disabled:bg-ink-faint/50",
  secondary: "bg-white text-ink border border-line hover:bg-canvas disabled:text-ink-faint",
  ghost: "text-ink-soft hover:bg-canvas hover:text-ink disabled:text-ink-faint",
  danger: "bg-white text-red-700 border border-red-200 hover:bg-red-50 disabled:text-ink-faint",
};

const BUTTON_BASE =
  "inline-flex min-h-10 items-center justify-center gap-1.5 rounded-lg px-3.5 text-[15px] font-medium transition-colors disabled:cursor-not-allowed focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";

export function Button({
  variant = "secondary",
  className,
  busy,
  children,
  ...rest
}: ComponentProps<"button"> & { variant?: Variant; busy?: boolean }) {
  return (
    <button type="button" className={cx(BUTTON_BASE, VARIANT[variant], className)} {...rest}>
      {busy && <Spinner className="h-4 w-4" />}
      {children}
    </button>
  );
}

export function ButtonLink({
  variant = "secondary",
  className,
  ...rest
}: ComponentProps<typeof Link> & { variant?: Variant }) {
  return <Link className={cx(BUTTON_BASE, VARIANT[variant], className)} {...rest} />;
}

export function Spinner({ className }: { className?: string }) {
  return (
    <svg className={cx("animate-spin", className || "h-5 w-5")} viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.2" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

export function Avatar({ name, size = 32, ring, index }: { name: string; size?: number; ring?: boolean; index?: number }) {
  const { bg, fg } = avatarColors(name, index);
  return (
    <span
      title={name}
      className={cx(
        "inline-flex shrink-0 items-center justify-center rounded-full font-semibold select-none",
        ring && "ring-2 ring-white",
      )}
      style={{ width: size, height: size, background: bg, color: fg, fontSize: Math.round(size * 0.42) }}
    >
      {initial(name)}
    </span>
  );
}

export function AvatarStack({ names, size = 28 }: { names: string[]; size?: number }) {
  return (
    <span className="flex -space-x-1.5">
      {names.map((n, i) => (
        <Avatar key={n} name={n} size={size} index={i} ring />
      ))}
    </span>
  );
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "accent" | "red" | "amber" | "green" }) {
  const tones = {
    neutral: "bg-canvas text-ink-soft border-line",
    accent: "bg-accent-soft text-accent border-accent-line",
    red: "bg-red-50 text-red-700 border-red-200",
    amber: "bg-amber-50 text-amber-800 border-amber-200",
    green: "bg-emerald-50 text-emerald-700 border-emerald-200",
  };
  return (
    <span className={cx("inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap", tones[tone])}>
      {children}
    </span>
  );
}

export function ErrorBox({ children, onRetry }: { children: ReactNode; onRetry?: () => void }) {
  return (
    <div role="alert" className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-[15px] text-red-800">
      <div className="flex flex-wrap items-center gap-3">
        <span className="min-w-0 flex-1">{children}</span>
        {onRetry && (
          <Button variant="secondary" className="min-h-9" onClick={onRetry}>
            重试
          </Button>
        )}
      </div>
    </div>
  );
}

/** 后端接口还没上线时的友好提示 */
export function ComingSoon({ title = "功能即将上线", children }: { title?: string; children?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-accent-line bg-accent-soft/60 px-4 py-4 text-[15px]">
      <div className="font-semibold text-accent">{title}</div>
      {children && <div className="mt-1 text-ink-soft">{children}</div>}
    </div>
  );
}

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cx("rounded-xl border border-line bg-white", className)}>{children}</div>;
}

/**
 * 表单项。里面是单个输入框时用 <label>；里面是一组按钮（单选组、标签输入）时传 group，
 * 改用 role="group"，避免点标签文字时误触第一个按钮。
 */
export function Field({
  label,
  hint,
  required,
  children,
  className,
  group,
}: {
  label: string;
  hint?: ReactNode;
  required?: boolean;
  children: ReactNode;
  className?: string;
  group?: boolean;
}) {
  const inner = (
    <>
      <span className="mb-1 block text-sm font-medium text-ink">
        {label}
        {required && <span className="ml-0.5 text-red-600">*</span>}
      </span>
      {children}
      {hint && <span className="mt-1 block text-xs text-ink-faint">{hint}</span>}
    </>
  );
  if (group) {
    return (
      <div role="group" aria-label={label} className={cx("block", className)}>
        {inner}
      </div>
    );
  }
  return <label className={cx("block", className)}>{inner}</label>;
}

/** 输入框和下拉框的基础样式，不含宽度。要固定宽度时用它加 w-24 之类，不要在 inputClass 上加（会和 w-full 冲突）。 */
export const fieldClass =
  "block min-h-10 rounded-lg border border-line bg-white px-3 py-2 text-ink placeholder:text-ink-faint focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20 disabled:bg-canvas";

/** 占满整行的输入框。 */
export const inputClass = `${fieldClass} w-full`;

export function Switch({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="flex min-h-10 w-full items-center justify-between gap-3 rounded-lg px-1 text-left text-[15px] disabled:opacity-50"
    >
      <span>{label}</span>
      <span
        className={cx(
          "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors",
          checked ? "bg-accent" : "bg-gray-300",
        )}
      >
        <span
          className={cx(
            "inline-block h-5 w-5 rounded-full bg-white shadow transition-transform",
            checked ? "translate-x-5.5" : "translate-x-0.5",
          )}
        />
      </span>
    </button>
  );
}
