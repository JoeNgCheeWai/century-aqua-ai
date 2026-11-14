'use client';

import { useState } from 'react';
import PrivacyToggle from './PrivacyToggle';

export type PrivacyMode = 'private' | 'shared';

type Props = {
  initialMode?: PrivacyMode;
  onModeChange?: (mode: PrivacyMode) => void;
};

// Holds the visual private/shared state for the Boardroom.
// UI only: it does not change any backend behaviour yet.
export default function BoardroomPrivacyControl({
  initialMode = 'private',
  onModeChange,
}: Props) {
  const [privacyMode, setPrivacyMode] = useState<PrivacyMode>(initialMode);

  const handleChange = (m: string) => {
    const next: PrivacyMode = m === 'shared' ? 'shared' : 'private';
    setPrivacyMode(next);
    onModeChange?.(next);
  };

  return (
    <div className="flex items-center gap-3 rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-2">
      <PrivacyToggle mode={privacyMode} onChange={handleChange} />
      <span className="text-xs text-slate-400" aria-live="polite">
        Session mode: {privacyMode === 'shared' ? 'Shared' : 'Private'}
      </span>
    </div>
  );
}
