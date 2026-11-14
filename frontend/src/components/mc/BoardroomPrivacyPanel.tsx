'use client';

import { useState } from 'react';
import PrivacyModeSwitch from './PrivacyModeSwitch';

export default function BoardroomPrivacyPanel() {
  const [privacyMode, setPrivacyMode] = useState<'private' | 'shared'>('private');

  return (
    <div className="flex items-center gap-3">
      <PrivacyModeSwitch mode={privacyMode} onChange={setPrivacyMode} />
    </div>
  );
}
