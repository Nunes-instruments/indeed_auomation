import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      {
        source: "/backend/:path*",
        destination: "http://127.0.0.1:5286/:path*",
      },
    ];
  },
};

export default nextConfig;
