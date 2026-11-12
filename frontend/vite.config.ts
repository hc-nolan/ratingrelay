import tailwindcss from '@tailwindcss/vite';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

// The API runs on a different port from the dev server, so /api is proxied.
// Override with BACKEND_URL when the API isn't on the default port.
const backendUrl = process.env.BACKEND_URL ?? 'http://localhost:8100';

export default defineConfig({
	plugins: [sveltekit(), tailwindcss()],
	server: {
		port: 5173,
		strictPort: true,
		proxy: {
			'/api': backendUrl
		}
	}
});
