'use client';

export interface PrivacyModeSwitchProps {
  mode: 'private' | 'shared';
  onChange: (newMode: 'private' | 'shared') => void;
}

export default function PrivacyModeSwitch({ mode, onChange }: PrivacyModeSwitchProps) {
  const isShared = mode === 'shared';

  return (
    <div className="flex items-center gap-2">
      <span className={isShared ? 'text-sm text-slate-500' : 'text-sm font-medium text-cyan-300'}>
        Private
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={isShared}
        aria-label="Share this session"
        onClick={() => onChange(isShared ? 'private' : 'shared')}
        className={`relative h-6 w-12 rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-cyan-400 ${
          isShared ? 'bg-amber-500' : 'bg-slate-600'
        }`}
      >
        <span
          className={`absolute top-1 h-4 w-4 rounded-full bg-white transition-all ${
            isShared ? 'right-1' : 'left-1'
          }`}
        />
      </button>
      <span className={isShared ? 'text-sm font-medium text-amber-300' : 'text-sm text-slate-500'}>
        Shared
      </span>
    </div>
  );
}
