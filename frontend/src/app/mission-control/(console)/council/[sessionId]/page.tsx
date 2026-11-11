import { Suspense } from "react";
import SessionView from "./SessionView";

export const metadata = { title: "Boardroom session | Mission Control" };

// The id is only known at request time (Cache Components), so it is read behind Suspense.
async function Session({ params }: { params: Promise<{ sessionId: string }> }) {
  const { sessionId } = await params;
  return <SessionView sessionId={sessionId} />;
}

export default function CouncilSessionPage({ params }: { params: Promise<{ sessionId: string }> }) {
  return (
    <Suspense fallback={<p className="text-sm text-slate-500">Loading…</p>}>
      <Session params={params} />
    </Suspense>
  );
}
