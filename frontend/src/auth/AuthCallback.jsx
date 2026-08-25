import React, { useEffect, useRef, useState } from "react";
import { handleAuthCallback, getCurrentUser } from "./cognitoAuth";

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
        window.history.replaceState({}, document.title, "/");
        window.setTimeout(() => window.location.assign("/"), 500);
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
      <h1>{status}</h1>
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
