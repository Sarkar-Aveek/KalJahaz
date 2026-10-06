/* Where this site finds its backend.

   Empty when the backend serves this page itself (local development, or a tunnel carrying the
   whole site). On Cloudflare Pages (kaljahaz.pages.dev) the backend is this PC through a quick
   tunnel whose address changes on every start: deploy\go-live.ps1 rewrites it here and redeploys.
   No trailing slash. */
window.KALJAHAZ_API = location.hostname.endsWith(".pages.dev") ? "https://mart-parliament-underwear-worm.trycloudflare.com" : "";
