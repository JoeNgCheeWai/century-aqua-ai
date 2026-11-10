"use client";

import { Card, buttonGhost, inputClass } from "@/components/mc/ui";
import { AGENT_KEYS } from "@/lib/mc-types";

export interface Filters {
  agent: string;
  from: string;
  to: string;
  status: string;
  authorization: string; // "" | "true" | "false"
  search: string;
}

export const NO_FILTERS: Filters = { agent: "", from: "", to: "", status: "", authorization: "", search: "" };

interface Props {
  filters: Filters;
  typed: string;
  onTyped: (text: string) => void;
  onChange: (patch: Partial<Filters>) => void;
  onClear: () => void;
  onRefresh: () => void;
}

/** The audit browser's search box and filters: agent, decision, authorization request, and a date range. */
export default function AuditFilters({ filters, typed, onTyped, onChange, onClear, onRefresh }: Props) {
  const active = Object.values(filters).some(Boolean) || typed.trim() !== "";
  return (
    <Card className="mb-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-6">
        <input
          value={typed}
          onChange={(e) => onTyped(e.target.value)}
          placeholder="Search questions and answers…"
          aria-label="Search"
          className={`${inputClass} sm:col-span-2`}
        />
        <select value={filters.agent} onChange={(e) => onChange({ agent: e.target.value })} className={inputClass} aria-label="Agent">
          <option value="">All agents</option>
          {AGENT_KEYS.map((a) => (
            <option key={a} value={a}>
              {a.toUpperCase()}
            </option>
          ))}
        </select>
        <select value={filters.status} onChange={(e) => onChange({ status: e.target.value })} className={inputClass} aria-label="Decision">
          <option value="">Any decision</option>
          {["Pending", "Approved", "Modified", "Rejected"].map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <select value={filters.authorization} onChange={(e) => onChange({ authorization: e.target.value })} className={inputClass} aria-label="Authorization request">
          <option value="">With or without a request</option>
          <option value="true">Has an authorization request</option>
          <option value="false">No authorization request</option>
        </select>
        <button onClick={onRefresh} className={buttonGhost}>
          Refresh
        </button>
        <label className="text-xs text-slate-400">
          From
          <input type="date" value={filters.from} onChange={(e) => onChange({ from: e.target.value })} className={`${inputClass} mt-1`} />
        </label>
        <label className="text-xs text-slate-400">
          To (that whole day is included)
          <input type="date" value={filters.to} onChange={(e) => onChange({ to: e.target.value })} className={`${inputClass} mt-1`} />
        </label>
        {active && (
          <button onClick={onClear} className={`${buttonGhost} self-end`}>
            Clear filters
          </button>
        )}
      </div>
    </Card>
  );
}
