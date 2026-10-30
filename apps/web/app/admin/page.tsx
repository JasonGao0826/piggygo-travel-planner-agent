import type { Metadata } from "next";
import { AdminView } from "@/components/AdminView";

export const metadata: Metadata = { title: "管理", robots: { index: false, follow: false } };

export default function AdminPage() {
  return <AdminView />;
}
