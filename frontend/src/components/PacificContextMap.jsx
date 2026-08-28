export default function PacificContextMap({ compact = false }) {
  const titleId = compact ? "pacific-map-title-compact" : "pacific-map-title";
  const descriptionId = compact ? "pacific-map-description-compact" : "pacific-map-description";

  return (
    <section className={compact ? "pacific-context compact" : "pacific-context"}>
      <div className="context-copy">
        <p className="eyebrow">Regional context</p>
        <h2>A cross-habitat field range</h2>
        <p>
          The assignment scenario follows connected observation environments along North
          America&apos;s Pacific Coast — from temperate rainforest and marine sanctuaries to
          Southern California&apos;s desert basins.
        </p>
        <ul className="context-legend" aria-label="Highlighted habitat regions">
          <li><span className="legend-swatch rainforest" />Pacific Northwest rainforests</li>
          <li><span className="legend-swatch marine" />Coastal marine sanctuaries</li>
          <li><span className="legend-swatch desert" />Southern California desert basins</li>
        </ul>
      </div>
      <figure className="context-map">
        <svg viewBox="0 0 720 460" role="img" aria-labelledby={`${titleId} ${descriptionId}`}>
          <title id={titleId}>Illustrative map of the North American Pacific Coast</title>
          <desc id={descriptionId}>
            A stylised map highlights rainforest in the Pacific Northwest, marine habitat along
            the Pacific coastline, and desert habitat in Southern California.
          </desc>
          <rect width="720" height="460" className="map-ocean" />
          <path
            className="map-land"
            d="M392 5 L720 5 L720 455 L505 455 L493 423 L466 400 L459 369 L431 343 L425 306 L399 278 L394 238 L373 207 L379 168 L359 136 L367 101 L349 69 L366 35 Z"
          />
          <path className="map-coastline" d="M392 5 L366 35 L349 69 L367 101 L359 136 L379 168 L373 207 L394 238 L399 278 L425 306 L431 343 L459 369 L466 400 L493 423 L505 455" />
          <path className="range-rainforest" d="M362 38 C331 54 320 89 347 126 L373 115 L359 77 L380 49 Z" />
          <path className="range-marine" d="M332 37 C297 116 307 210 348 288 C366 321 382 346 417 380" />
          <path className="range-desert" d="M431 319 C466 299 520 307 551 350 C527 390 485 405 454 378 Z" />
          <g className="map-label map-label-rainforest">
            <circle cx="346" cy="85" r="5" />
            <path d="M340 85 H220" />
            <text x="208" y="79" textAnchor="end">Pacific Northwest</text>
            <text x="208" y="99" textAnchor="end">temperate rainforest</text>
          </g>
          <g className="map-label map-label-marine">
            <circle cx="340" cy="235" r="5" />
            <path d="M334 235 H182" />
            <text x="170" y="229" textAnchor="end">Pacific coastal</text>
            <text x="170" y="249" textAnchor="end">marine habitat</text>
          </g>
          <g className="map-label map-label-desert">
            <circle cx="472" cy="355" r="5" />
            <path d="M478 355 H563" />
            <text x="575" y="349">Southern California</text>
            <text x="575" y="369">desert basins</text>
          </g>
          <text x="82" y="405" className="map-water-label">PACIFIC OCEAN</text>
        </svg>
        <figcaption>
          Illustrative ecosystem context based on the assignment scenario — not live archive
          coverage or species-range data.
        </figcaption>
      </figure>
    </section>
  );
}
