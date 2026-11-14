import {useEffect, useRef, useState} from 'react';
import {
  ArrowDown, ArrowRight, ArrowUpRight, Check, FileCheck2, GitBranch,
  GitPullRequest, ListTodo, Pause, Play, Search, ShieldCheck, Timer, Zap,
} from 'lucide-react';
import './landing.css';

const stages = [
  {name: 'Start a job', detail: 'A request. A ticket. A signal.', icon: ListTodo},
  {name: 'Find the context', detail: 'Source evidence + approved knowledge.', icon: Search},
  {name: 'Prepare a patch', detail: 'Plan and edit in isolation.', icon: GitPullRequest},
  {name: 'Check the work', detail: 'Tests, evidence + independent review.', icon: ShieldCheck},
  {name: 'Review & apply', detail: 'Your approval. The exact revision.', icon: Check},
];

function HeroVideo() {
  const video = useRef<HTMLVideoElement>(null);
  const [paused, setPaused] = useState(true);
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const syncMotion = () => {
      if (motion.matches) video.current?.pause();
      else void video.current?.play().catch(() => setPaused(true));
    };
    syncMotion();
    motion.addEventListener('change', syncMotion);
    return () => motion.removeEventListener('change', syncMotion);
  }, []);

  function togglePlayback() {
    if (!video.current) return;
    if (video.current.paused) void video.current.play().catch(() => setPaused(true));
    else video.current.pause();
  }

  return <div className="landing-video">
    <video
      ref={video}
      src="/landing/hero.mp4"
      poster="/landing/hero-poster.jpg"
      muted loop playsInline preload="metadata"
      aria-hidden="true"
      onPlay={() => setPaused(false)}
      onPause={() => setPaused(true)}
      onError={() => setUnavailable(true)}
    />
    <a className="landing-scroll" href="#workspace" aria-label="Explore the workspace">
      <ArrowDown size={19} aria-hidden="true"/>
    </a>
    {!unavailable && <button className="landing-video-control" onClick={togglePlayback} aria-label={paused ? 'Play video' : 'Pause video'}>
      {paused ? <Play size={16} aria-hidden="true"/> : <Pause size={16} aria-hidden="true"/>}
    </button>}
  </div>;
}

function JobFlow() {
  return <div className="landing-flow-diagram">
    <ol className="landing-flow" aria-label="A full Duck Tape job">
      {stages.map(({name, detail, icon: Icon}, index) => <li key={name}>
        <div className="landing-flow-top"><span>0{index + 1}</span><Icon size={22} strokeWidth={1.5} aria-hidden="true"/></div>
        <h3>{name}</h3>
        <p>{detail}</p>
        {index < stages.length - 1 && <ArrowRight className="landing-flow-arrow" size={18} aria-hidden="true"/>}
      </li>)}
    </ol>
    <div className="landing-revision-loop">
      <svg viewBox="0 0 600 65" preserveAspectRatio="none" aria-hidden="true">
        <path d="M590 0V26Q590 44 572 44H28Q10 44 10 26V4M4 10L10 3L16 10"/>
      </svg>
      <span>Request a revision. Run it again.</span>
    </div>
    <div className="landing-flow-note"><GitBranch size={14} aria-hidden="true"/> Apply to a managed branch. You control the merge.</div>
  </div>;
}

function Benchmarks() {
  return <div className="landing-stat-grid">
    <article className="landing-stat-card">
      <div className="landing-card-label"><span>BENCHMARK</span><span>01 / EXAMPLE</span></div>
      <div className="landing-card-icon"><Zap size={25} strokeWidth={1.5} aria-hidden="true"/></div>
      <h3>Faster checks.</h3>
      <div className="landing-stat-value">2.4<span>×</span></div>
      <div className="landing-bars" aria-label="Example check runtime: before 120 seconds, after 50 seconds">
        <div><span>Before</span><i/><span>120s</span></div>
        <div><span>After</span><i/><span>50s</span></div>
      </div>
      <p>Same suite. Less waiting.</p>
    </article>
    <article className="landing-stat-card">
      <div className="landing-card-label"><span>VERIFICATION</span><span>02 / EXAMPLE</span></div>
      <div className="landing-card-icon"><FileCheck2 size={25} strokeWidth={1.5} aria-hidden="true"/></div>
      <h3>Evidence, included.</h3>
      <div className="landing-stat-value">18<span>/20</span></div>
      <div className="landing-check-grid" aria-hidden="true">
        {Array.from({length: 20}, (_, index) => <span key={index} className={index < 18 ? 'is-passing' : ''}>{index < 18 ? <Check size={12}/> : '–'}</span>)}
      </div>
      <p>18 checks passed. 2 need work.</p>
    </article>
    <article className="landing-stat-card">
      <div className="landing-card-label"><span>TURNAROUND</span><span>03 / EXAMPLE</span></div>
      <div className="landing-card-icon"><Timer size={25} strokeWidth={1.5} aria-hidden="true"/></div>
      <h3>Ready for your review.</h3>
      <div className="landing-stat-value landing-stat-time">4<span>m</span> 12<span>s</span></div>
      <svg className="landing-sparkline" viewBox="0 0 300 66" aria-hidden="true">
        <path className="landing-chart-grid" d="M0 15H300M0 40H300M0 65H300"/>
        <path className="landing-chart-area" d="M0 7L28 14L55 11L83 29L110 23L137 35L165 31L193 47L220 43L248 52L275 48L300 56V66H0Z"/>
        <path className="landing-chart-line" d="M0 7L28 14L55 11L83 29L110 23L137 35L165 31L193 47L220 43L248 52L275 48L300 56"/>
      </svg>
      <p>Median time from job to review.</p>
    </article>
  </div>;
}

export function Landing() {
  return <div className="landing" id="top">
    <a className="landing-skip" href="#workspace">Skip to content</a>
    <div className="landing-opening">
      <header className="landing-header">
        <a className="landing-brand" href="/" aria-label="Duck Tape home"><GitBranch size={25} strokeWidth={1.7} aria-hidden="true"/>Duck Tape</a>
        <nav className="landing-nav" aria-label="Main navigation">
          <a href="#workspace">Workspace</a>
          <a href="#workflow">Workflow</a>
          <a href="#benchmarks">Benchmarks</a>
        </nav>
        <a className="landing-button" href="/dashboard">Get started <ArrowUpRight size={15} aria-hidden="true"/></a>
      </header>
      <section className="landing-hero" aria-labelledby="landing-headline">
        <h1 id="landing-headline">Duck Tape is on <span>Duck Tape</span></h1>
        <HeroVideo/>
      </section>
    </div>

    <main className="landing-main">
      <section className="landing-workspace landing-section" id="workspace" aria-labelledby="workspace-heading" tabIndex={-1}>
        <div className="landing-section-heading">
          <div><span className="landing-kicker">01 / THE WORKSPACE</span><h2 id="workspace-heading">Everything in view.</h2></div>
          <a className="landing-text-link" href="/dashboard">Open workspace <ArrowUpRight size={18} aria-hidden="true"/></a>
        </div>
        <div className="landing-preview">
          <div className="landing-preview-bar">
            <div className="landing-window-dots" aria-hidden="true"><i/><i/><i/></div>
            <div className="landing-preview-tab"><GitBranch size={14} aria-hidden="true"/>Duck Tape <span>/ workspace</span></div>
            <span className="landing-preview-label">Application preview</span>
          </div>
          <div className="landing-preview-viewport">
            <iframe title="Application preview" src="/dashboard?preview=1" loading="lazy" tabIndex={-1} inert/>
          </div>
        </div>
      </section>

      <section className="landing-workflow landing-section" id="workflow" aria-labelledby="workflow-heading">
        <span className="landing-kicker">02 / THE WORKFLOW</span>
        <h2 id="workflow-heading">A job. Start to finish.</h2>
        <JobFlow/>
      </section>

      <section className="landing-benchmarks landing-section" id="benchmarks" aria-labelledby="benchmarks-heading">
        <div className="landing-section-heading">
          <div><span className="landing-kicker">03 / THE NUMBERS</span><h2 id="benchmarks-heading">Show the work.</h2></div>
          <p className="landing-example-note">Illustrative data, not measured results.</p>
        </div>
        <Benchmarks/>
      </section>
    </main>

    <footer className="landing-footer">
      <a className="landing-brand" href="/"><GitBranch size={22} strokeWidth={1.7} aria-hidden="true"/>Duck Tape</a>
      <span>© {new Date().getFullYear()} Duck Tape</span>
      <nav aria-label="Footer navigation"><a href="/dashboard">Workspace <ArrowUpRight size={14} aria-hidden="true"/></a><a href="#top">Back to top ↑</a></nav>
    </footer>
  </div>;
}
