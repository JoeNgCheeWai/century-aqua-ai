import ComingSoon from "@/components/staff/ComingSoon";

export default function InternalSettingsPage() {
  return (
    <ComingSoon
      title="Settings"
      phase="Later"
      description="Personal preferences for the workspace."
      planned={[
        "Default agent and chat preferences",
        "Notification preferences for meeting rooms",
      ]}
    />
  );
}
