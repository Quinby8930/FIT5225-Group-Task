import { signIn, signInWithGoogle, signUp } from "../../auth/cognitoAuth";
import { appConfig } from "../../auth/cognitoConfig";
import { BrandMark } from "../../components/AppHeader";
import HabitatCarousel from "../../components/HabitatCarousel";
import PacificContextMap from "../../components/PacificContextMap";

export default function LandingPage({ sessionReason }) {
  return (
    <div className="landing">
      <header className="landing-masthead">
        <a className="brand brand-link" href={appConfig.homePath} aria-label="Pacific BioArchive home">
          <BrandMark size={30} />
          <span className="brand-name">Pacific BioArchive</span>
        </a>
        <nav className="mast-actions" aria-label="Account access">
          <button type="button" className="link-button" onClick={signIn}>Sign in</button>
          <button type="button" className="btn btn-primary" onClick={signUp}>Create account</button>
        </nav>
      </header>

      <main>
        <section className="hero hero-grid">
          <div className="hero-copy">
            <p className="eyebrow">Multi-cloud wildlife media archive</p>
            <h1>Cross-habitat wildlife observation, kept structured, durable, and searchable.</h1>
            {sessionReason === "expired" && (
              <p className="session-alert" role="alert">Your session has expired. Please sign in again.</p>
            )}
            <p className="lede">
              Canopy camera traps, underwater video systems, and desert field cameras generate
              observation media that is easily lost or fragmented. Pacific BioArchive ingests it,
              tags the species automatically, and keeps it searchable. Storage, database, and
              queries run on AWS; species inference runs on Alibaba Cloud.
            </p>
            <div className="hero-cta">
              <button type="button" className="btn btn-primary" onClick={signUp}>Create account</button>
              <button type="button" className="btn btn-secondary" onClick={signIn}>Sign in</button>
              <button type="button" className="btn btn-quiet" onClick={signInWithGoogle}>Sign in with Google</button>
            </div>
          </div>
          <HabitatCarousel />
        </section>

        <div className="motif-wrap">
          <div className="motif" aria-hidden="true"><span className="m1" /><span className="m2" /><span className="m3" /></div>
          <p className="motif-cap">Decorative habitat band — canopy, reef, desert. Not archive data.</p>
        </div>

        <div className="dossier">
          <section className="section" aria-labelledby="s-purpose">
            <div className="num" aria-hidden="true">01</div>
            <div>
              <h2 id="s-purpose">Why it exists</h2>
              <p>
                Field sensors produce extensive repositories of visual intelligence, but research
                teams routinely lose data to damaged storage cards, unindexed drive arrays, and
                disconnected field stations. This platform establishes a unified ingestion
                pipeline so observational evidence remains structured, durable, and readily
                accessible.
              </p>
            </div>
          </section>

          <PacificContextMap />

          <section className="section" aria-labelledby="s-capabilities">
            <div className="num" aria-hidden="true">02</div>
            <div>
              <h2 id="s-capabilities">What you can do</h2>
              <div className="caps">
                <div className="cap">
                  <h3>Upload &amp; automatic tagging</h3>
                  <p>
                    Upload images and videos. A per-account duplicate check compares each file's
                    checksum against your previous uploads. Thumbnails are generated for images;
                    videos are sampled at one frame per second. A machine-learning model adds
                    species tags.
                  </p>
                </div>
                <div className="cap">
                  <h3>Search the archive</h3>
                  <p>
                    Find media by species, or by tags with minimum counts using AND logic — for
                    example wombat ≥ 2 and dingo ≥ 1. Match by image detects the species tags in
                    your reference image and finds archive records carrying those tags; the
                    reference image is never added to the archive. You can also resolve a trusted
                    archive thumbnail back to its full-size original.
                  </p>
                </div>
                <div className="cap">
                  <h3>Manage &amp; follow</h3>
                  <p>
                    Add or remove tags in bulk on your own uploads, and delete them together with
                    their stored files. Subscribe to a species and an in-app notification appears
                    when new matching media arrives.
                  </p>
                </div>
              </div>
            </div>
          </section>

          <section className="section" aria-labelledby="s-pipeline">
            <div className="num" aria-hidden="true">03</div>
            <div>
              <h2 id="s-pipeline">How it works</h2>
              <ol className="pipeline">
                <li className="step">
                  <span className="n">Step 1</span>
                  <span className="t">Sign in</span>
                  <span className="d">Email or Google account, verified by AWS Cognito.</span>
                </li>
                <li className="step">
                  <span className="n">Step 2</span>
                  <span className="t">Upload</span>
                  <span className="d">Files go to private cloud storage after a per-account duplicate check.</span>
                </li>
                <li className="step">
                  <span className="n">Step 3</span>
                  <span className="t">Automated processing</span>
                  <span className="d">Thumbnails, video frames, species tagging, metadata records.</span>
                </li>
                <li className="step">
                  <span className="n">Step 4</span>
                  <span className="t">Search &amp; manage</span>
                  <span className="d">Query the archive, preview privately, curate your own uploads.</span>
                </li>
              </ol>
              <p className="fineprint">
                per-account checksum dedup · aspect-ratio thumbnails · 1 frame/sec video sampling ·
                AND tag queries · private bucket · 15-minute signed access
              </p>
            </div>
          </section>

          <section className="section" aria-labelledby="s-access">
            <div className="num" aria-hidden="true">04</div>
            <div>
              <h2 id="s-access">Access</h2>
              <div className="access">
                <p className="note">
                  Media objects remain in private storage. Signed-in users can preview completed
                  archive media through short-lived signed URLs; only owners can modify or delete
                  their uploads. New accounts verify their email address before the first sign-in.
                </p>
                <div className="cta-row">
                  <button type="button" className="btn btn-primary" onClick={signUp}>Create account</button>
                  <button type="button" className="btn btn-secondary" onClick={signIn}>Sign in</button>
                  <button type="button" className="btn btn-quiet" onClick={signInWithGoogle}>Sign in with Google</button>
                </div>
              </div>
            </div>
          </section>
        </div>
      </main>

      <footer className="landing-footer">
        <span>FIT5225 group project — multi-cloud serverless platform (AWS + Alibaba Cloud).</span>
        <span>Previews use short-lived signed links.</span>
      </footer>
    </div>
  );
}
