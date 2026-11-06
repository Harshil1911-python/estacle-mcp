import { Container, getContainer } from "@cloudflare/containers";

/**
 * Estacle Flask app running inside a Cloudflare Container.
 * Single logical instance keeps SQLite consistent.
 */
export class EstacleContainer extends Container {
  defaultPort = 8080;
  sleepAfter = "15m";
  envVars = {
    PORT: "8080",
    SESSION_COOKIE_SECURE: "1",
    PREFERRED_URL_SCHEME: "https",
    TRUSTED_PROXY_HOPS: "1",
  };
}

export default {
  async fetch(request, env) {
    const container = getContainer(env.ESTACLE, "main");
    return container.fetch(request);
  },
};
