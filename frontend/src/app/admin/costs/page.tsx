import ComingSoon from "@/components/staff/ComingSoon";

export default function AdminCostsPage() {
  return (
    <ComingSoon
      title="Costs"
      phase="Phase 3"
      description="Estimated AI spend. Estimates depend on the query log, which does not record anything yet."
      planned={[
        "Today's estimated cost and a monthly projection",
        "Cost split between Dr. Aqua and the AI_CAO",
        "An alert when daily spend passes a threshold you choose",
      ]}
    />
  );
}
