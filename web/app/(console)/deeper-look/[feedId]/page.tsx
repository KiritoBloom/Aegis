"use client";

import { useParams } from "next/navigation";
import { DeeperLook } from "@/components/deeper-look";

export default function DeeperLookPage() {
  const { feedId } = useParams<{ feedId: string }>();
  return <DeeperLook key={feedId} feedId={feedId} />;
}
