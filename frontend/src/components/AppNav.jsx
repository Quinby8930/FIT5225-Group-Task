const views = [
  ["explore", "Explore"],
  ["upload", "Upload"],
  ["manage", "Manage"],
  ["notifications", "Notifications"],
];

export default function AppNav({ activeView, manageCount, onNavigate, diagnostics }) {
  return (
    <div className="app-nav">
      <nav className="primary-nav" aria-label="Primary navigation">
        <p className="nav-label" aria-hidden="true">Workspace</p>
        {views.map(([id, label]) => (
          <button
            key={id}
            type="button"
            className={activeView === id ? "nav-item active" : "nav-item"}
            aria-current={activeView === id ? "page" : undefined}
            onClick={() => onNavigate(id)}
          >
            <span>{label}</span>
            {id === "manage" && manageCount > 0 && (
              <span className="badge" aria-label={`${manageCount} selected record(s)`}>{manageCount}</span>
            )}
          </button>
        ))}
      </nav>
      {diagnostics}
    </div>
  );
}
