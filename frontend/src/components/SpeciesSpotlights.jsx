import { SUGGESTED_SPECIES } from "../lib/homeExperience.mjs";

function publicAsset(path) {
  return `${import.meta.env.BASE_URL}${path}`;
}

export default function SpeciesSpotlights({ disabled = false, onSelect }) {
  return (
    <section className="species-spotlights" aria-labelledby="species-spotlight-heading">
      <div className="spotlight-heading">
        <div>
          <p className="eyebrow">Model label examples</p>
          <h2 id="species-spotlight-heading">Suggested species searches</h2>
        </div>
        <p>Reference species from the supplied Australian inference labels, not popularity data.</p>
      </div>
      <div className="spotlight-grid">
        {SUGGESTED_SPECIES.map((species) => (
          <article key={species.id} className="spotlight-card">
            <img src={publicAsset(species.image)} alt={species.alt} width="900" height="650" loading="lazy" />
            <div>
              <h3>{species.name}</h3>
              <p>{species.description}</p>
              <small className="spotlight-credit">{species.credit}</small>
              <button
                type="button"
                className="link-button"
                disabled={disabled}
                onClick={() => onSelect(species.query)}
              >
                Explore this species
              </button>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
