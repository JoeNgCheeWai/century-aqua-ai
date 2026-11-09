import ComingSoon from "@/components/staff/ComingSoon";

export default function AdminAnalyticsPage() {
  return (
    <ComingSoon
      title="Analytics"
      phase="Phase 3"
      description="How the platform is being used. Needs the query log, which does not record anything yet."
      planned={[
        "Line chart of queries per hour and per day",
        "Most active users with their roles and message counts",
        "Split of queries between Dr. Aqua and the AI_CAO",
        "Common error messages and failure rates by endpoint",
      ]}
    />
  );
}
