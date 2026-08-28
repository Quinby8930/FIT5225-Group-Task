import AuthControls from "../auth/AuthControls";

export function BrandMark({ size = 26 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 30 30" aria-hidden="true" focusable="false">
      <rect width="30" height="30" rx="3" fill="#123f33" />
      <ellipse cx="15" cy="18.5" rx="5.2" ry="4.2" fill="#f6f7f5" />
      <circle cx="9.6" cy="11.8" r="2" fill="#f6f7f5" />
      <circle cx="15" cy="10" r="2" fill="#f6f7f5" />
      <circle cx="20.4" cy="11.8" r="2" fill="#f6f7f5" />
    </svg>
  );
}

export default function AppHeader({ user, activeView, onNavigate }) {
  return (
    <header className="app-header">
      <button
        type="button"
        className="brand brand-home"
        aria-label="Go to Home"
        aria-current={activeView === "home" ? "page" : undefined}
        onClick={() => onNavigate("home")}
      >
        <BrandMark size={26} />
        <span className="brand-name">Pacific BioArchive</span>
      </button>
      <AuthControls user={user} />
    </header>
  );
}
