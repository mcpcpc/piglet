// Classify provider error messages so the UI can offer the fix that matches the cause.

const AUTH_ERROR =
  /\b(401|403)\b|unauthori[sz]ed|forbidden|authentication|invalid[ _-]?(api[ _-]?)?key|api[ _-]?key|credential|not logged in/i;

/** Whether `message` reads like a rejected or missing credential, so "Sign in" can fix it. */
export function isAuthError(message: string): boolean {
  return AUTH_ERROR.test(message);
}
