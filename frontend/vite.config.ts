import tailwindcss from '@tailwindcss/vite';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

// The API runs on a different port from the dev server, so /api is proxied.
// Override with BACKEND_URL when the API isn't on the default port.
const backendUrl = process.env.BACKEND_URL ?? 'http://localhost:8100';

// Bind every interface so the dev server is reachable from the LAN, the same way
// the API already is (host = 0.0.0.0 in .config). Vite answers only to Host
// headers it recognises: IP addresses always pass, hostnames have to be listed,
// so mDNS names work out of the box and anything else is named here.
const allowedHosts = (process.env.DEV_ALLOWED_HOSTS ?? '.local')
	.split(',')
	.map((host) => host.trim())
	.filter(Boolean);

export default defineConfig({
	plugins: [sveltekit(), tailwindcss()],
	server: {
		host: true,
		allowedHosts,
		port: 5173,
		strictPort: true,
		proxy: {
			'/api': {
				target: backendUrl,
				// Keep the browser's Host header on the hop. The API builds the Plex OAuth
				// callback URL from it when base_url is the localhost default, and a
				// rewritten "localhost:<port>" sends a browser on the LAN to its own box.
				changeOrigin: false
			}
		}
	}
});
