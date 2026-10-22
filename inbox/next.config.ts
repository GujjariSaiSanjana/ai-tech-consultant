import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The Docker image sets NEXT_OUTPUT=standalone; local `npm start` uses the normal server.
  ...(process.env.NEXT_OUTPUT === "standalone" ? { output: "standalone" as const } : {}),
  serverExternalPackages: ["@modelcontextprotocol/sdk"],
};

export default nextConfig;
