import { useState } from "react";
import { getAuthTest } from "./api/apiClient";
import AuthCallback from "./auth/AuthCallback";
import AuthControls from "./auth/AuthControls";

export default function App() {
  const [apiResult, setApiResult] = useState(null);
  const [apiError, setApiError] = useState(null);

  if (window.location.pathname === "/callback") {
    return <AuthCallback />;
  }

  return (
    <main className="app-shell">
      <header className="app-header">
        <div>
          <p className="eyebrow">Pacific BioArchive</p>
          <h1>Wildlife media authentication test</h1>
        </div>
        <AuthControls />
      </header>

      <section className="panel">
        <h2>Cognito protected API check</h2>
        <p>
          Use this button after signing in to verify that API Gateway accepts
          the Cognito JWT and forwards user claims to Lambda.
        </p>
        <button
          type="button"
          onClick={() => {
            setApiResult(null);
            setApiError(null);
            getAuthTest()
              .then(setApiResult)
              .catch((error) => setApiError(error.message));
          }}
        >
          Test protected API
        </button>
        {apiError && <pre className="error-output">{apiError}</pre>}
        {apiResult && (
          <pre className="success-output">
            {JSON.stringify(apiResult, null, 2)}
          </pre>
        )}
      </section>
    </main>
  );
}
