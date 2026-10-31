"use client";

import { useApi } from "@/lib/useApi";

type Household = { profile?: { display_name?: string } };

/** The masthead must say the household's name, not the demo resident's. */
export function HouseholdLabel() {
  const household = useApi<Household>("/api/household", 60000);
  const first = household.data?.profile?.display_name?.trim().split(/\s+/)[0];
  return <span className="mono">{first ? `${first.toUpperCase()}'S HOME` : "THIS HOME"}</span>;
}
