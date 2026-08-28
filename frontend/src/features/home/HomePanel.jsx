import HabitatCarousel from "../../components/HabitatCarousel";
import PacificContextMap from "../../components/PacificContextMap";

export default function HomePanel({ onNavigate }) {
  return (
    <div className="home-view">
      <section className="home-hero">
        <div className="home-hero-copy">
          <p className="eyebrow">Field workspace</p>
          <h1>Pacific BioArchive</h1>
          <p className="home-lede">
            Bring wildlife images and videos into one private, searchable workflow across AWS
            and Alibaba Cloud.
          </p>
          <div className="hero-cta">
            <button type="button" className="btn btn-primary" onClick={() => onNavigate("explore")}>Explore the archive</button>
            <button type="button" className="btn btn-secondary" onClick={() => onNavigate("upload")}>Upload media</button>
          </div>
        </div>
        <HabitatCarousel compact />
      </section>

      <section className="home-actions" aria-labelledby="home-actions-heading">
        <div className="home-actions-head">
          <p className="eyebrow">Start a task</p>
          <h2 id="home-actions-heading">Your archive workflow</h2>
        </div>
        <div className="home-action-grid">
          <button type="button" onClick={() => onNavigate("explore")}>
            <span>01</span><strong>Search</strong><small>Find completed media by species, tags, image, or thumbnail.</small>
          </button>
          <button type="button" onClick={() => onNavigate("upload")}>
            <span>02</span><strong>Ingest</strong><small>Upload an image or video to the automated processing pipeline.</small>
          </button>
          <button type="button" onClick={() => onNavigate("notifications")}>
            <span>03</span><strong>Watch</strong><small>Subscribe to species and review matching archive notifications.</small>
          </button>
        </div>
      </section>

      <PacificContextMap compact />
    </div>
  );
}
