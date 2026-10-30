import type { Metadata } from "next";
import { IntakeWizard } from "@/components/IntakeWizard";

export const metadata: Metadata = { title: "新建行程" };

export default function NewTripPage() {
  return <IntakeWizard />;
}
