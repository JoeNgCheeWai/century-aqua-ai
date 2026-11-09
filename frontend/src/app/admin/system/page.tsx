import ComingSoon from "@/components/staff/ComingSoon";

export default function AdminSystemPage() {
  return (
    <ComingSoon
      title="System Health"
      phase="Phase 3"
      description="Is everything the platform depends on up and configured?"
      planned={[
        "Live checks of the backend, database, Pinecone and OpenAI",
        "Which required settings are configured (values never shown)",
        "Recent server errors",
      ]}
    />
  );
}
