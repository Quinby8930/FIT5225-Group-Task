import React from "react";
import {
  getCurrentUser,
  signIn,
  signInWithGoogle,
  signOut,
} from "./cognitoAuth";

export default function AuthControls() {
  const user = getCurrentUser();

  if (!user) {
    return (
      <section className="auth-actions">
        <button type="button" onClick={signIn}>
          Sign in
        </button>
        <button type="button" className="secondary-button" onClick={signInWithGoogle}>
          Sign in with Google
        </button>
      </section>
    );
  }

  return (
    <section className="auth-actions">
      <span>{user.email}</span>
      <button type="button" onClick={signOut}>
        Sign out
      </button>
    </section>
  );
}
