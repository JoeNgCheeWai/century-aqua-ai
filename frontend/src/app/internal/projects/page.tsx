import ComingSoon from "@/components/staff/ComingSoon";

export default function InternalProjectsPage() {
  return (
    <ComingSoon
      title="Projects"
      phase="Phase 2"
      description="Shared projects that group related chats and documents for a team."
      planned={[
        "Create a project with a name, description and members",
        "Attach chats to a project so the whole team can see them",
        "Database tables for projects and project chats already exist",
      ]}
    />
  );
}
