# Customising the portal

All frontend customisation is in `frontend/app/src/portal.config.ts` and needs no change to the packages.

* **Branding**: name, logo URL and theme colours (CSS variables in `packages/ui/src/theme.css`; light and dark).
* **Pages**: the portal builds its navigation from `defaultPages` in `packages/pages/src/index.ts`. Remove entries,
  reorder them, or append your own page object (`id`, `title`, `path`, `component`, optional `requires`). Pages that need
  a role are hidden for people without it; the backend enforces the same rule.
* **Auth**: `AuthProvider` is an interface. `msalAuth.ts` is the Entra implementation; `DemoAuth` serves demo mode. Replace
  it to sign in another way.
* **Data**: pages call the typed `ApiClient` through `usePortal()` and `useLoad()`. A new page can call any `/api/` route.
* **Backend**: `create_app(...)` accepts replacements for the store, directory, authenticator, Azure clients, permission
  checker and dispatcher. Add a notification channel in `notifications.Dispatcher.send`.
* **Settings**: every setting is a `PORTAL_*` variable (see `config.py`).
