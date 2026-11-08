"use client";

import { useEffect, useRef, useState } from "react";
import type { Usage } from "@/lib/plans";

export interface Message {
  role: "user" | "ai";
  text: string;
}

/**
 * Chat state and the call to /api/chat (the rate-limited route handler).
 * A 429 RATE_LIMIT_EXCEEDED response is surfaced as `limitResetTime` and the
 * message goes back into the input box (it was not sent).
 * "New Chat" is done by the parent remounting the chat with a new React key.
 */
export function useChat(onUsage: (usage: Usage) => void) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [limitResetTime, setLimitResetTime] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function send(text: string) {
    const message = text.trim();
    if (!message || loading) return;

    const history = messages.slice(-20).map((m) => ({
      role: m.role === "ai" ? "assistant" : "user",
      content: m.text,
    }));
    setMessages((prev) => [...prev, { role: "user", text: message }]);
    setInput("");
    setLoading(true);

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, history }),
      });

      if (response.status === 429) {
        const data = (await response.json()) as { resetTime?: string };
        setLimitResetTime(data.resetTime ?? new Date().toISOString());
        setMessages((prev) => prev.slice(0, -1)); // the message was not sent
        setInput(message);
        return;
      }
      if (!response.ok) throw new Error(`Chat error: ${response.status}`);

      const data = (await response.json()) as { reply: string; usage?: Usage };
      setMessages((prev) => [...prev, { role: "ai", text: data.reply }]);
      setLimitResetTime(null);
      if (data.usage) onUsage(data.usage);
    } catch (error) {
      console.error("Chat error:", error);
      setMessages((prev) => [
        ...prev,
        { role: "ai", text: "Sorry, I'm having trouble reaching the intelligence engine. Please try again in a moment." },
      ]);
    } finally {
      setLoading(false);
    }
  }

  return { messages, input, setInput, loading, limitResetTime, send, endRef };
}
