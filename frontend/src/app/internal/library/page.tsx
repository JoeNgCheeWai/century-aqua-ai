import ComingSoon from "@/components/staff/ComingSoon";

export default function InternalLibraryPage() {
  return (
    <ComingSoon
      title="Document Library"
      phase="Later"
      description="A view of the documents in the AI_CAO knowledge base. Managing them (ingesting, replacing, removing) belongs to the Command Centre."
      planned={[
        "Browse the ingested documents (trial data, patents, regulatory files, public literature)",
        "See which documents answered which questions",
      ]}
    />
  );
}
