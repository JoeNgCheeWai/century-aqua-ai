import ComingSoon from "@/components/staff/ComingSoon";

export default function AdminRagPage() {
  return (
    <ComingSoon
      title="RAG Management"
      phase="Phase 3"
      description="Control of the AI_CAO knowledge base. For now, documents are ingested from the command line (scripts/ingest_document.py and auto_fetch_literature.py in the backend folder)."
      planned={[
        "Table of ingested documents: file, type, chunk count, date, and source API for fetched papers",
        "Knowledge-base totals and last ingestion date",
        "Buttons to fetch open-access literature and to upload a PDF",
      ]}
    />
  );
}
