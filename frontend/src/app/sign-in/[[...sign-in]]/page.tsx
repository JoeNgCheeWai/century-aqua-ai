import { Suspense } from "react";
import { SignIn } from "@clerk/nextjs";
import { clerkAppearance } from "@/lib/clerk-appearance";
import { clerkConfigured } from "@/lib/clerk-config";
import AuthNotConfigured from "../../AuthNotConfigured";

export default function SignInPage() {
  if (!clerkConfigured) return <AuthNotConfigured />;
  return (
    <main className="flex min-h-screen items-center justify-center bg-[#0A2342] px-6 py-12">
      {/* <SignIn /> reads the request URL, so Cache Components needs a Suspense boundary. */}
      <Suspense fallback={<p className="text-white/60">Loading…</p>}>
        <SignIn appearance={clerkAppearance} />
      </Suspense>
    </main>
  );
}
