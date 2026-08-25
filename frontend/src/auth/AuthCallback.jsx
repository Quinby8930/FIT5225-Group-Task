import React, { useEffect, useState } from "react";
import { handleAuthCallback, getCurrentUser } from "./cognitoAuth";

export default function AuthCallback() {
  const [status, setStatus] = useState("Signing you in...");
  const [user, setUser] = useState(null);

  useEffect(() => {
    let active = true;

    handleAuthCallback()
      .then(() => {
        if (!active) return;
        setUser(getCurrentUser());
        setStatus("Signed in successfully.");
        window.history.replaceState({}, document.title, "/");
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
