import {useEffect, useRef, useState} from 'react';
import {
  ArrowDown, ArrowUpRight, BookOpen, Check, FileCheck2,
  Pause, Play, ShieldCheck, Ticket, Timer, Zap,
} from 'lucide-react';
import {WorkspacePreview} from './WorkspacePreview';
import './landing.css';

const stages = [
  {name: 'Understand', detail: 'Retrieve source evidence and approved repository knowledge.'},
  {name: 'Prepare', detail: 'Approve preparation, then plan and edit in isolation.'},
  {name: 'Check', detail: 'Run tests and an independent review. Revise when needed.'},
  {name: 'Review', detail: 'Review and approve the exact revision, then apply to a managed branch.'},
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
    <div className="landing-media-window">
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
    </div>
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
    <svg className="landing-flow-ribbons" viewBox="0 0 1100 260" preserveAspectRatio="none" aria-hidden="true">
      <defs>
        <linearGradient id="landing-warm"><stop stopColor="#a75662"/><stop offset="1" stopColor="#e9b8a9"/></linearGradient>
        <linearGradient id="landing-cool" gradientUnits="userSpaceOnUse" x1="80" x2="365"><stop stopColor="#689f9e"/><stop offset="1" stopColor="#c4ddd2"/></linearGradient>
        <linearGradient id="landing-violet"><stop stopColor="#443481"/><stop offset=".5" stopColor="#9b6bcd"/><stop offset="1" stopColor="#86b2b5"/></linearGradient>
        <linearGradient id="landing-path" gradientUnits="userSpaceOnUse" x1="365" x2="1000"><stop stopColor="#e3a995"/><stop offset=".55" stopColor="#7362c7"/><stop offset="1" stopColor="#b494ac"/></linearGradient>
      </defs>
      <use href="#landing-ribbons" className="landing-ribbon-glow"/>
      <g id="landing-ribbons" fill="none" strokeWidth="21" strokeLinecap="round">
        <path d="M80 55C225 55 195 130 365 130" stroke="url(#landing-warm)"/>
        <path d="M80 130H365" stroke="url(#landing-cool)"/>
        <path d="M80 205C220 205 205 130 365 130" stroke="url(#landing-violet)"/>
        <path d="M365 130H1000" stroke="url(#landing-path)"/>
        <path d="M575 130C645 0 715 0 785 130" stroke="url(#landing-violet)"/>
        <path d="M785 130C860 245 930 245 1000 130" stroke="url(#landing-warm)"/>
      </g>
    </svg>
    <ul className="landing-flow-sources" aria-label="Job sources">
      <li><Ticket size={17} aria-hidden="true"/>Tickets</li>
      <li><ShieldCheck size={17} aria-hidden="true"/>Security</li>
      <li><BookOpen size={17} aria-hidden="true"/>Research</li>
    </ul>
    <ol className="landing-flow" aria-label="A full Duck Tape job">
      {stages.map(({name, detail}) => <li key={name}>
        <h3>{name}</h3>
        <span className="landing-visually-hidden">{detail}</span>
      </li>)}
    </ol>
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
    </article>
    <article className="landing-stat-card">
      <div className="landing-card-label"><span>VERIFICATION</span><span>02 / EXAMPLE</span></div>
      <div className="landing-card-icon"><FileCheck2 size={25} strokeWidth={1.5} aria-hidden="true"/></div>
      <h3>Checks passed.</h3>
      <div className="landing-stat-value">18<span>/20</span></div>
      <div className="landing-check-grid" aria-hidden="true">
        {Array.from({length: 20}, (_, index) => <span key={index} className={index < 18 ? 'is-passing' : ''}>{index < 18 ? <Check size={12}/> : '–'}</span>)}
      </div>
      <p className="landing-visually-hidden">18 checks passed. 2 need work.</p>
    </article>
    <article className="landing-stat-card">
      <div className="landing-card-label"><span>TURNAROUND</span><span>03 / EXAMPLE</span></div>
      <div className="landing-card-icon"><Timer size={25} strokeWidth={1.5} aria-hidden="true"/></div>
      <h3>Time to review.</h3>
      <div className="landing-stat-value landing-stat-time">4<span>m</span> 12<span>s</span></div>
      <svg className="landing-sparkline" viewBox="0 0 300 66" aria-hidden="true">
        <path className="landing-chart-grid" d="M0 15H300M0 40H300M0 65H300"/>
        <path className="landing-chart-area" d="M0 7L28 14L55 11L83 29L110 23L137 35L165 31L193 47L220 43L248 52L275 48L300 56V66H0Z"/>
        <path className="landing-chart-line" d="M0 7L28 14L55 11L83 29L110 23L137 35L165 31L193 47L220 43L248 52L275 48L300 56"/>
      </svg>
      <p className="landing-visually-hidden">Median time from job to review.</p>
    </article>
  </div>;
}

export function Landing() {
  return <div className="landing" id="top">
    <div className="landing-atmosphere" aria-hidden="true"/>
    <a className="landing-skip" href="#workspace">Skip to content</a>
    <div className="landing-opening">
      <header className="landing-header">
        <a className="landing-logo" href="/" aria-label="Duck Tape home"><img src="/landing/logo.png" alt="Duck Tape" width="44" height="44"/></a>
        <nav className="landing-nav" aria-label="Main navigation">
          <a href="#workspace">Workspace</a>
          <a href="#workflow">Workflow</a>
          <a href="#benchmarks">Benchmarks</a>
        </nav>
      </header>
      <section className="landing-hero" aria-labelledby="landing-headline">
        <h1 id="landing-headline">Duck Tape is on Duck Tape</h1>
        <div className="landing-hero-actions">
          <a className="landing-button" href="/dashboard">Log in</a>
          <a className="landing-button landing-button-create" href="/dashboard">Start creating <ArrowUpRight size={16} aria-hidden="true"/></a>
        </div>
        <HeroVideo/>
      </section>
    </div>

    <main className="landing-main">
      <section className="landing-workspace landing-section" id="workspace" aria-labelledby="workspace-heading" tabIndex={-1}>
        <div className="landing-section-heading">
          <div><span className="landing-kicker">01 / THE WORKSPACE</span><h2 id="workspace-heading">Everything in view.</h2></div>
          <a className="landing-text-link" href="/dashboard">Open workspace <ArrowUpRight size={18} aria-hidden="true"/></a>
        </div>
        <WorkspacePreview/>
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
      <a className="landing-brand" href="/"><img src="/landing/logo.png" alt="" width="25" height="25"/>Duck Tape</a>
      <span>© {new Date().getFullYear()} Duck Tape</span>
      <nav aria-label="Footer navigation"><a href="/dashboard">Workspace <ArrowUpRight size={14} aria-hidden="true"/></a><a href="#top">Back to top ↑</a></nav>
    </footer>
  </div>;
}
