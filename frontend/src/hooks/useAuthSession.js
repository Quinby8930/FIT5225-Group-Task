import { useEffect, useState } from "react";
import { clearTokens, inspectStoredAuthSession } from "../auth/cognitoAuth";

export default function useAuthSession() {
  const [session, setSession] = useState(() => { const inspected = inspectStoredAuthSession(); return { user: inspected.user, reason: inspected.reason, shouldClear: inspected.shouldClear }; });
  useEffect(() => {
    let timer = null;
    const refresh = (reason = null) => {
      const inspected = inspectStoredAuthSession();
      const effectiveReason = inspected.user ? null : (reason || inspected.reason);
      setSession({ user: inspected.user, reason: effectiveReason, shouldClear: inspected.shouldClear });
      if (timer) window.clearTimeout(timer);
      if (inspected.shouldClear) { clearTokens(effectiveReason); return; }
      if (inspected.user?.exp) timer = window.setTimeout(() => clearTokens("expired"), Math.max(0, inspected.user.exp * 1000 - Date.now()));
    };
    const onAuth = (event) => refresh(event.detail?.reason || null);
    const onStorage = (event) => { if (event.key === "pacificBioArchive.tokens") refresh(null); };
    globalThis.addEventListener("pacificBioArchive:auth", onAuth);
    globalThis.addEventListener("storage", onStorage);
    if (session.shouldClear) clearTokens(session.reason); else refresh(null);
    return () => { if (timer) window.clearTimeout(timer); globalThis.removeEventListener("pacificBioArchive:auth", onAuth); globalThis.removeEventListener("storage", onStorage); };
  }, []);
  return session;
}
