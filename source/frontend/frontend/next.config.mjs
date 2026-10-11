import { existsSync } from "node:fs";
import { resolve } from "node:path";

// One shared .env lives in source/ for both backend and frontend.
const sharedEnv = resolve(process.cwd(), "..", ".env");
if (existsSync(sharedEnv)) process.loadEnvFile(sharedEnv);

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  env: { NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000" },
};
export default nextConfig;
