const RESOURCE_CLIENT_ID = "rixin-contract-comparison-api";

export interface CurrentUser {
  sub: string;
  username: string;
  displayName: string;
  email: string;
  departmentCode: string;
  departmentName: string;
  roles: ReadonlySet<string>;
}

type Claims = Record<string, unknown>;

/** Decoded claims are display-only. The backend remains authoritative for authorization. */
export function buildCurrentUser(accessToken: string, profile: Claims = {}): CurrentUser {
  const claims = decodeAccessToken(accessToken);
  const sub = stringClaim(claims, "sub") || stringClaim(profile, "sub");
  if (!sub.trim()) throw new Error("Access token sub is required.");

  const username = stringClaim(claims, "preferred_username") || stringClaim(profile, "preferred_username");
  const name = stringClaim(claims, "name") || stringClaim(profile, "name");
  return {
    sub,
    username,
    displayName: name || username || sub,
    email: stringClaim(claims, "email") || stringClaim(profile, "email"),
    departmentCode: stringClaim(claims, "department_code") || stringClaim(profile, "department_code"),
    departmentName: stringClaim(claims, "department_name") || stringClaim(profile, "department_name"),
    roles: rolesFromClaims(claims),
  };
}

export function hasRole(user: CurrentUser, role: string): boolean {
  return user.roles.has(role);
}

function decodeAccessToken(accessToken: string): Claims {
  const payload = accessToken.split(".")[1];
  if (!payload) throw new Error("Access token payload is invalid.");
  const padded = payload.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(payload.length / 4) * 4, "=");
  const bytes = Uint8Array.from(atob(padded), (character) => character.charCodeAt(0));
  const parsed: unknown = JSON.parse(new TextDecoder("utf-8").decode(bytes));
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("Access token claims are invalid.");
  return parsed as Claims;
}

function rolesFromClaims(claims: Claims): ReadonlySet<string> {
  const resourceAccess = claims.resource_access;
  if (!resourceAccess || typeof resourceAccess !== "object" || Array.isArray(resourceAccess)) return new Set();
  const clientAccess = (resourceAccess as Claims)[RESOURCE_CLIENT_ID];
  if (!clientAccess || typeof clientAccess !== "object" || Array.isArray(clientAccess)) return new Set();
  const rawRoles = (clientAccess as Claims).roles;
  return new Set(Array.isArray(rawRoles) ? rawRoles.filter((role): role is string => typeof role === "string") : []);
}

function stringClaim(claims: Claims, name: string): string {
  const value = claims[name];
  return typeof value === "string" ? value : "";
}
