import { getCurrentUser, signIn, signOut } from "./cognitoAuth";

export default function AuthControls() {
  const user = getCurrentUser();

  if (!user) {
    return (
      <button type="button" onClick={signIn}>
        Sign in
      </button>
    );
  }

  return (
    <section>
      <span>{user.email}</span>
      <button type="button" onClick={signOut}>
        Sign out
      </button>
    </section>
  );
}
