import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react-swc";
export default defineConfig(function (_a) {
    var mode = _a.mode;
    var env = loadEnv(mode, ".", "");
    var apiTarget = env.VITE_API_PROXY_TARGET || "http://127.0.0.1:8000";
    return {
        plugins: [react()],
        server: {
            host: "127.0.0.1",
            port: 5173,
            proxy: {
                "/v1": {
                    target: apiTarget,
                    changeOrigin: true,
                },
            },
        },
        preview: {
            host: "127.0.0.1",
            port: 4173,
        },
    };
});
