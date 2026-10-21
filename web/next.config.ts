import type { NextConfig } from "next";

const dev = process.env.NODE_ENV === "development";

const turbopack: NextConfig["turbopack"] = {
  rules: { "*.css": { loaders: ["@tailwindcss/turbopack"], as: "*.css" } },
};

// Production is a static export served by the FastAPI process. In development the
// Next dev server proxies /api to a locally running `aiedit serve`.
const nextConfig: NextConfig = dev
  ? {
      turbopack,
      async rewrites() {
        return [{ source: "/api/:path*", destination: "http://127.0.0.1:8000/api/:path*" }];
      },
    }
  : { turbopack, output: "export", trailingSlash: true, images: { unoptimized: true } };

export default nextConfig;
