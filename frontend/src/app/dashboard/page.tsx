import { Suspense } from "react";
import { auth, currentUser } from "@clerk/nextjs/server";
import { redirect } from "next/navigation";
import { clerkConfigured } from "@/lib/clerk-config";
import type { Usage } from "@/lib/plans";
import { getUsage } from "@/lib/usage";
import AuthNotConfigured from "../AuthNotConfigured";
import DashboardShell from "./DashboardShell";

export const metadata = { title: "Dashboard | Century Aqua" };

/** Reads the signed-in user and their REAL usage from the database. */
async function DashboardLoader() {
  if (!clerkConfigured) return <AuthNotConfigured />;

  const { userId } = await auth();
  if (!userId) redirect("/sign-in");

  const user = await currentUser();

  let usage: Usage | null = null;
  try {
    usage = await getUsage(userId);
  } catch (error) {
    // The dashboard still opens if the database is down; the badge says so.
    console.error("dashboard: usage unavailable:", (error as Error).name);
  }

  const firstName = user?.firstName ?? "";
  const userName =
    [user?.firstName, user?.lastName].filter(Boolean).join(" ") ||
    user?.primaryEmailAddress?.emailAddress ||
    "Account";

  return <DashboardShell initialUsage={usage} firstName={firstName} userName={userName} />;
}

export default function DashboardPage() {
  // Request-time data (session + database) must sit behind Suspense (Cache Components).
  return (
    <Suspense
      fallback={
        <div className="flex h-screen items-center justify-center bg-[#0A2342] text-white/60">
          <span className="animate-pulse">Loading your dashboard…</span>
        </div>
      }
    >
      <DashboardLoader />
    </Suspense>
  );
}
