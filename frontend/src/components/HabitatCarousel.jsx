import { useEffect, useState } from "react";
import { carouselIndexAfter, HABITAT_SLIDES } from "../lib/homeExperience.mjs";

const ROTATION_INTERVAL_MS = 8000;

function publicAsset(path) {
  return `${import.meta.env.BASE_URL}${path}`;
}

export default function HabitatCarousel({ compact = false }) {
  const [activeIndex, setActiveIndex] = useState(0);
  const [paused, setPaused] = useState(true);
  const [interactionPaused, setInteractionPaused] = useState(false);
  const [documentHidden, setDocumentHidden] = useState(() => document.visibilityState === "hidden");
  const [reduceMotion, setReduceMotion] = useState(
    () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches || false
  );
  const canRotate = !paused && !interactionPaused && !documentHidden && !reduceMotion;

  useEffect(() => {
    const media = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    if (!media) return undefined;
    const update = (event) => setReduceMotion(event.matches);
    media.addEventListener?.("change", update);
    return () => media.removeEventListener?.("change", update);
  }, []);

  useEffect(() => {
    const update = () => setDocumentHidden(document.visibilityState === "hidden");
    document.addEventListener("visibilitychange", update);
    return () => document.removeEventListener("visibilitychange", update);
  }, []);

  useEffect(() => {
    if (!canRotate || HABITAT_SLIDES.length < 2) return undefined;
    const timer = window.setInterval(
      () => setActiveIndex((current) => carouselIndexAfter(current, 1, HABITAT_SLIDES.length)),
      ROTATION_INTERVAL_MS
    );
    return () => window.clearInterval(timer);
  }, [canRotate]);

  function move(delta) {
    setActiveIndex((current) => carouselIndexAfter(current, delta, HABITAT_SLIDES.length));
  }

  return (
    <section
      className={compact ? "habitat-carousel compact" : "habitat-carousel"}
      aria-label="Pacific Coast habitat photographs"
      aria-roledescription="carousel"
      onMouseEnter={() => setInteractionPaused(true)}
      onMouseLeave={() => setInteractionPaused(false)}
      onFocusCapture={() => setPaused(true)}
    >
      <div className="carousel-stage">
        {!reduceMotion && (
          <button
            type="button"
            className="carousel-pause"
            onClick={() => setPaused((current) => !current)}
          >
            {paused ? "Play rotation" : "Pause rotation"}
          </button>
        )}
        {HABITAT_SLIDES.map((slide, index) => {
          const active = index === activeIndex;
          return (
            <figure
              key={slide.id}
              className={active ? "carousel-slide active" : "carousel-slide"}
              aria-hidden={!active}
              aria-label={`${index + 1} of ${HABITAT_SLIDES.length}`}
              aria-roledescription="slide"
              inert={!active ? true : undefined}
            >
              <img
                src={publicAsset(slide.image)}
                alt={slide.alt}
                width="1600"
                height="1080"
                loading={index === 0 ? "eager" : "lazy"}
                fetchPriority={index === 0 ? "high" : "auto"}
              />
              <figcaption>
                <span className="carousel-kicker">Habitat {String(index + 1).padStart(2, "0")}</span>
                <strong>{slide.title}</strong>
                <span>{slide.description}</span>
                <small>{slide.credit}</small>
              </figcaption>
            </figure>
          );
        })}
        <div className="carousel-buttons">
          <button type="button" className="carousel-arrow previous" onClick={() => move(-1)} aria-label="Previous habitat image">
            <svg aria-hidden="true" viewBox="0 0 24 24"><path d="m15 18-6-6 6-6" /></svg>
          </button>
          <button type="button" className="carousel-arrow next" onClick={() => move(1)} aria-label="Next habitat image">
            <svg aria-hidden="true" viewBox="0 0 24 24"><path d="m9 18 6-6-6-6" /></svg>
          </button>
        </div>
      </div>

      <div className="carousel-controls">
        <div className="carousel-dots" role="group" aria-label="Choose a habitat image">
          {HABITAT_SLIDES.map((slide, index) => (
            <button
              key={slide.id}
              type="button"
              className={index === activeIndex ? "active" : ""}
              aria-label={`Show ${slide.title}`}
              aria-current={index === activeIndex ? "true" : undefined}
              onClick={() => setActiveIndex(index)}
            />
          ))}
        </div>
      </div>
      <p className="carousel-disclaimer">
        Illustrative habitat imagery — not archive media.{" "}
        <a href={publicAsset("image-credits.txt")} target="_blank" rel="noreferrer">Image credits</a>.
      </p>
    </section>
  );
}
