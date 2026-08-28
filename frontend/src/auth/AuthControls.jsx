import React from "react";
import {
  signIn,
  signInWithGoogle,
  signUp,
  signOut,
} from "./cognitoAuth";

export default function AuthControls({ user }) {

  if (!user) {
    return (
      <section className="auth-actions">
        <button type="button" className="secondary-button" onClick={signUp}>
          Create account
        </button>
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
