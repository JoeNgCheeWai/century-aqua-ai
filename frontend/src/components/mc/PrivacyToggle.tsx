'use client';

export type PrivacyMode = 'private' | 'shared';

export interface PrivacyToggleProps {
  value: PrivacyMode;
  onChange: (next: PrivacyMode) => void;
  disabled?: boolean;
  className?: string;
}

export default function PrivacyToggle({
  value,
  onChange,
  disabled = false,
  className = '',
}: PrivacyToggleProps) {
  const isShared = value === 'shared';
  const next: PrivacyMode = isShared ? 'private' : 'shared';

  return (
    <div className={`flex items-center gap-3 ${className}`}>
      <span
        className={`text-sm font-medium ${
          isShared ? 'text-slate-500' : 'text-emerald-400'
        }`}
      >
        Private
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={isShared}
        aria-label="Session visibility: Private or Shared"
        disabled={disabled}
        onClick={() => onChange(next)}
        className="relative h-6 w-12 rounded-full border border-slate-700 bg-slate-900 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400 disabled:cursor-not-allowed disabled:opacity-50"
      >
        <span
          aria-hidden="true"
          className={`absolute top-1 h-4 w-4 rounded-full transition-all ${
            isShared ? 'left-7 bg-amber-400' : 'left-1 bg-emerald-500'
          }`}
        />
      </button>
      <span
        className={`text-sm font-medium ${
          isShared ? 'text-amber-400' : 'text-slate-500'
        }`}
      >
        Shared
      </span>
    </div>
  );
}
