import { defaultPages, type PageDefinition } from "@foundry-finops/pages";

/**
 * The one file to edit to make the portal yours: the menu and routes, the look, and where the API lives.
 *
 * - `pages`: copy, filter, reorder or add to `defaultPages`. Add your own page with a `component` that uses
 *   `usePortal()` to get the API client.
 * - `theme`: CSS variables from @foundry-finops/ui/theme.css (colours, font, radius), applied at start-up.
 * - `apiBaseUrl`: leave empty when the portal serves its own frontend.
 */
export interface AppConfig {
  pages: PageDefinition[];
  theme: Record<string, string>;
  apiBaseUrl: string;
  /** Shown instead of the name the backend reports, when set. */
  brand?: string;
}

const config: AppConfig = {
  pages: defaultPages,
  theme: {
    // "--ff-accent": "#0b6a0b",
    // "--ff-font": "Inter, system-ui, sans-serif",
  },
  apiBaseUrl: import.meta.env.VITE_API_BASE_URL ?? "",
};

export default config;
