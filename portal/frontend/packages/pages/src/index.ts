import type { Me } from "@foundry-finops/api";
import type { ComponentType } from "react";
import { AccessPage } from "./AccessPage";
import { AdminPage } from "./AdminPage";
import { BudgetsTagsPage } from "./BudgetsTagsPage";
import { CostCentresPage } from "./CostCentresPage";
import { CostPage } from "./CostPage";
import { FleetPage } from "./FleetPage";
import { NotificationsPage } from "./NotificationsPage";
import { OperationsPage } from "./OperationsPage";
import { ProjectMonitorPage } from "./ProjectMonitorPage";

export * from "./AccessPage";
export * from "./AdminPage";
export * from "./BudgetsTagsPage";
export * from "./CostCentresPage";
export * from "./CostControlForm";
export * from "./CostControlsPanel";
export * from "./CostPage";
export * from "./FleetPage";
export * from "./NotificationsPage";
export * from "./OperationsPage";
export * from "./ProjectMonitorPage";
export * from "./common";
export * from "./context";

export interface PageDefinition {
  id: string;
  /** Route path. */
  path: string;
  title: string;
  /** Navigation section; leave out to keep the page off the menu (for example a detail page). */
  section?: string;
  component: ComponentType;
  /** Hide the page from people it isn't meant for. The API still enforces access. */
  visible?: (me: Me) => boolean;
}

/**
 * The pages the portal ships. Copy, filter, reorder or add to this list in your own app: the shell builds its menu
 * and routes from whatever you pass it.
 */
export const defaultPages: PageDefinition[] = [
  { id: "fleet", path: "/fleet", title: "Fleet", section: "Manage", component: FleetPage },
  { id: "operations", path: "/operations", title: "Operations", section: "Manage", component: OperationsPage, visible: (me) => me.account_level },
  { id: "cost", path: "/cost", title: "Cost", section: "Manage", component: CostPage },
  { id: "projects", path: "/projects/:project?", title: "Project monitor", section: "Build", component: ProjectMonitorPage },
  { id: "cost-centres", path: "/cost-centres", title: "Cost centres", section: "Allocate", component: CostCentresPage },
  { id: "notifications", path: "/notifications", title: "Notifications", section: "Allocate", component: NotificationsPage },
  { id: "budgets", path: "/budgets", title: "Budgets & tags", section: "Allocate", component: BudgetsTagsPage, visible: (me) => me.account_level },
  { id: "admin", path: "/admin", title: "Admin", section: "Administer", component: AdminPage, visible: (me) => me.role === "admin" },
  { id: "access", path: "/access", title: "Access help", section: "Administer", component: AccessPage },
];
