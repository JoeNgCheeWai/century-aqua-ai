import ComingSoon from "@/components/staff/ComingSoon";

export default function InternalMeetingsPage() {
  return (
    <ComingSoon
      title="Meeting Rooms"
      phase="Phase 2"
      description="Shared chats where several colleagues and one AI agent talk together."
      planned={[
        "Create a room, invite colleagues by email and pick the AI agent (Dr. Aqua or AI_CAO)",
        "Everyone in the room sees new messages within about two seconds (polling)",
        "Database tables for rooms and messages already exist",
      ]}
    />
  );
}
