"use client";

import type { Usage } from "@/lib/plans";
import { SUGGESTIONS } from "./mock-data";
import { useChat } from "./useChat";

interface ChatAreaProps {
  firstName: string;
  usage: Usage | null;
  onUsage: (usage: Usage) => void;
  onOpenSidebar: () => void;
}

function formatReset(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

export default function ChatArea({ firstName, usage, onUsage, onOpenSidebar }: ChatAreaProps) {
  const { messages, input, setInput, loading, limitResetTime, send, endRef } = useChat(onUsage);

  const outOfMessages = usage !== null && !usage.unlimited && (usage.remaining ?? 1) <= 0;
  const limitNotice = limitResetTime ?? (outOfMessages ? usage?.resetTime ?? null : null);

  return (
    <section className="flex h-full min-w-0 flex-1 flex-col bg-[#0A2342]">
      <header className="flex items-center gap-3 border-b border-white/10 px-4 py-3 md:hidden">
        <button onClick={onOpenSidebar} aria-label="Open sidebar" className="text-white/70 hover:text-white">☰</button>
        <span className="text-sm font-semibold text-white">Dr. Aqua</span>
      </header>

      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col px-4 py-8">
          {messages.length === 0 ? (
            <div className="my-auto text-center">
              <span className="mx-auto mb-5 block h-3 w-3 rounded-full bg-[#00C9A7] shadow-[0_0_18px_#00C9A7]" />
              <h1 className="text-3xl font-semibold text-white sm:text-4xl">
                Welcome back{firstName ? `, ${firstName}` : ""}
              </h1>
              <p className="mx-auto mt-3 max-w-md text-white/60">
                Ask Dr. Aqua about species, water quality, feed, health and farm operations.
              </p>
              <div className="mx-auto mt-8 grid max-w-xl gap-2">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => send(s)}
                    disabled={loading || outOfMessages}
                    className="rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-left text-sm text-white/80 transition hover:border-[#00C9A7]/50 hover:bg-white/10 disabled:opacity-40"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="space-y-5">
              {messages.map((m, i) => (
                <div key={i} className={m.role === "user" ? "flex justify-end" : "flex"}>
                  <div
                    className={`max-w-[85%] whitespace-pre-wrap rounded-2xl px-4 py-3 text-sm leading-relaxed ${
                      m.role === "user" ? "rounded-tr-sm bg-[#00C9A7] text-[#0A2342]" : "rounded-tl-sm bg-white/10 text-white/90"
                    }`}
                  >
                    {m.text}
                  </div>
                </div>
              ))}
              {loading && (
                <div className="flex">
                  <div className="rounded-2xl rounded-tl-sm bg-white/10 px-4 py-3 text-sm text-white/60">
                    <span className="animate-pulse">Dr. Aqua is thinking…</span>
                  </div>
                </div>
              )}
              <div ref={endRef} />
            </div>
          )}
        </div>
      </div>

      <div className="border-t border-white/10 px-4 py-4">
        <div className="mx-auto max-w-3xl">
          {limitNotice && (
            <p role="alert" className="mb-3 rounded-lg border border-amber-300/30 bg-amber-300/10 px-3 py-2 text-sm text-amber-200">
              You&apos;ve used all your messages for this session. Your allowance resets at {formatReset(limitNotice)}.
            </p>
          )}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              send(input);
            }}
            className="flex gap-2"
          >
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={outOfMessages ? "Message limit reached" : "Ask about aquaculture…"}
              disabled={loading || outOfMessages}
              maxLength={4000}
              className="flex-1 rounded-full border border-white/15 bg-white/5 px-5 py-3 text-sm text-white placeholder-white/40 focus:border-[#00C9A7] focus:outline-none disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={loading || outOfMessages || !input.trim()}
              className="rounded-full bg-[#00C9A7] px-6 py-3 text-sm font-medium text-[#0A2342] transition hover:brightness-110 disabled:opacity-50"
            >
              Send
            </button>
          </form>
        </div>
      </div>
    </section>
  );
}
