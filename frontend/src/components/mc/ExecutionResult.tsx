import type { ExecutionOutcome } from "@/lib/mc-types";

/** Only a link that really points at GitHub is made clickable, so a tampered stored value can never become a link elsewhere. */
export function isGithubPrUrl(value: unknown): value is string {
  return typeof value === "string" && /^https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/pull\/\d+$/.test(value);
}

const TOOL_LABEL: Record<string, string> = {
  github_create_branch: "Created the branch",
  github_write_file: "Wrote a file",
  github_open_pr: "Opened the pull request",
};

/**
 * What happened after a plan was approved (Phase 6.6). A pull request that was opened is NOT a change that is live: it still
 * has to be reviewed and merged by a person, and this says so. A failure shows which steps finished, because a half-finished
 * run leaves a branch on GitHub that a human may want to look at or delete.
 */
export default function ExecutionResult({ outcome }: { outcome: ExecutionOutcome }) {
  const steps = outcome.steps ?? [];
  if (outcome.status === "running") {
    return <p className="text-sm text-amber-200">Opening the pull request… If this stays here, the run was interrupted: check GitHub for the branch, nothing was merged.</p>;
  }
  if (outcome.status === "succeeded") {
    return (
      <div role="status" className="space-y-1 text-sm text-emerald-100">
        <p>
          ✅ A pull request was opened.{" "}
          {isGithubPrUrl(outcome.pr_url) ? (
            <a href={outcome.pr_url} target="_blank" rel="noopener noreferrer" className="font-medium text-emerald-200 underline">
              Open it on GitHub →
            </a>
          ) : (
            <span className="text-amber-200">(its address could not be read; look for branch {outcome.branch ?? "agent/…"} on GitHub)</span>
          )}
        </p>
        <p className="text-xs text-slate-400">
          It is <strong>not merged</strong> and nothing is live. Review it, check that the build passed, then merge it yourself.
        </p>
      </div>
    );
  }
  return (
    <div role="alert" className="space-y-1 text-sm text-red-200">
      <p>
        ❌{" "}
        {outcome.status === "unconfigured"
          ? "The pull request was not opened: the backend has no GitHub token."
          : outcome.status === "refused"
            ? "The pull request was not opened: the plan was refused by the safety checks."
            : outcome.status === "already_claimed"
              ? "This approval was already executed, so nothing was run again."
              : "The pull request could not be completed."}
      </p>
      {outcome.error && <p className="text-xs text-red-300/80">{outcome.error}</p>}
      {steps.length > 0 && (
        <ul className="list-disc pl-5 text-xs text-slate-300">
          {steps.map((s, i) => (
            <li key={i}>
              {s.ok ? "Done" : "Failed"}: {TOOL_LABEL[s.tool] ?? s.tool} {s.message ? `(${s.message})` : ""}
            </li>
          ))}
        </ul>
      )}
      <p className="text-xs text-slate-400">The decision itself is recorded. Nothing was merged.</p>
    </div>
  );
}
