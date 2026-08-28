import React, { useEffect, useRef, useState } from "react";
import { handleAuthCallback, getCurrentUser, signIn } from "./cognitoAuth";
import { appConfig } from "./cognitoConfig";
import { postAuthHomePath } from "../lib/appRoutes.mjs";

export default function AuthCallback() {
  const [status, setStatus] = useState("Signing you in...");
  const [user, setUser] = useState(null);
  const initialUrl = useRef(window.location.href);
  const navigationStarted = useRef(false);

  useEffect(() => {
    let active = true;

    handleAuthCallback(initialUrl.current)
      .then(() => {
        if (!active || navigationStarted.current) return;
        navigationStarted.current = true;
        setUser(getCurrentUser());
        setStatus("Signed in successfully.");
        const homePath = postAuthHomePath(appConfig);
        window.history.replaceState({}, document.title, homePath);
        window.setTimeout(() => window.location.assign(homePath), 500);
      })
      .catch((error) => {
        if (!active) return;
        setStatus(error.message);
      });

    return () => {
      active = false;
    };
  }, []);

  return (
    <main>
      <h1 role="status" aria-live="polite">{status}</h1>
      {!user && status !== "Signing you in..." && <button type="button" onClick={signIn}>Sign in again</button>}
      {user && (
        <dl>
          <dt>Email</dt>
          <dd>{user.email}</dd>
          <dt>User ID</dt>
          <dd>{user.sub}</dd>
        </dl>
      )}
    </main>
  );
}
