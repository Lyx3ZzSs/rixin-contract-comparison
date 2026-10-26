function readEnv(name: string): string | undefined {
  const value = import.meta.env[name];
  if (typeof value !== "string") {
    return undefined;
  }
  const trimmed = value.trim();
  return trimmed ? trimmed : undefined;
}

export function normalizePathPrefix(value: string | undefined): string {
  if (!value || value === "/") {
    return "";
  }
  const withLeadingSlash = value.startsWith("/") ? value : `/${value}`;
  return withLeadingSlash.replace(/\/+$/, "");
}

export function normalizeBaseUrl(value: string): string {
  return value.replace(/\/+$/, "");
}

export function getAppBasePath(): string {
  return normalizePathPrefix(readEnv("VITE_BASE_PATH"));
}

export function getConfiguredApiBasePath(): string | undefined {
  const apiBasePath = readEnv("VITE_API_BASE_PATH");
  return apiBasePath ? normalizeBaseUrl(apiBasePath) : undefined;
}

export function getLegacyApiBaseUrl(): string | undefined {
  const apiBaseUrl = readEnv("VITE_API_BASE_URL");
  return apiBaseUrl ? normalizeBaseUrl(apiBaseUrl) : undefined;
}

export function withAppBasePath(path: string): string {
  const basePath = getAppBasePath();
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  if (!basePath) {
    return normalizedPath;
  }
  if (normalizedPath === "/") {
    return `${basePath}/`;
  }
  return `${basePath}${normalizedPath}`;
}

export function stripAppBasePath(pathname: string): string {
  const basePath = getAppBasePath();
  if (!basePath) {
    return pathname;
  }
  if (pathname === basePath || pathname === `${basePath}/`) {
    return "/";
  }
  if (pathname.startsWith(`${basePath}/`)) {
    return pathname.slice(basePath.length) || "/";
  }
  return pathname;
}
