import { redirect } from "next/navigation";

// /mission-control is just the front door: the proxy has already checked the session by the time this runs.
export default function MissionControlIndex() {
  redirect("/mission-control/dashboard");
}
