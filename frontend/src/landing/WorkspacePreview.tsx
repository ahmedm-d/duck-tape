import {GitBranch} from 'lucide-react';

// Keep the embedded app's presentation separate from saved workspace preferences.
const previewTheme = `
  :root.landing-demo {
    --bg: #111216; --surface: #191a20; --surface2: #212228;
    --text: #f0f0f5; --muted: #a4a4b0; --border: #ffffff12;
    --accent: #c5c3eb; --accent-bg: #b6b1e315; --shadow: none;
    color-scheme: dark; background: var(--bg); color: var(--text);
    font-family: Arial, Helvetica, sans-serif;
  }
  .landing-demo body { color: var(--text); background: linear-gradient(120deg, #25252b, #111216 65%); }
  .landing-demo .app { background: transparent; }
  .landing-demo .sidebar { background: #11121688; border-right: 1px solid var(--border); }
  .landing-demo .sidebar nav button { color: #b9b9c3; }
  .landing-demo .sidebar nav button.selected { background: #ffffff0c; color: #f0f0f5; }
  .landing-demo .brand, .landing-demo .brand-mark { color: #e2e2ed; }
  .landing-demo .workspace-label, .landing-demo .profile small { color: #9b9ba9; }
  .landing-demo .beta { color: #b3b3bf; border-color: #ffffff24; }
  .landing-demo .topbar { background: #ffffff03; }
  .landing-demo .panel, .landing-demo .metric, .landing-demo .repository-card {
    background: linear-gradient(120deg, #ffffff07, #ffffff01);
    border-color: var(--border); border-radius: 12px; box-shadow: inset 0 1px 0 #ffffff03;
  }
  .landing-demo .primary { background: #77749b60; border-color: #b2add12e; color: #fff; }
  .landing-demo .tag, .landing-demo .tag.accent { background: #ffffff06; border-color: #ffffff16; color: #c5c3d9; }
  .landing-demo .status.good { color: #a5c19d; }
  .landing-demo .status.warm { color: #dac4a2; }
  .landing-demo .status.bad { color: #dfaaa3; }
`;

export function WorkspacePreview() {
  return <div className="landing-preview">
    <div className="landing-preview-bar">
      <div className="landing-window-dots" aria-hidden="true"><i/><i/><i/></div>
      <div className="landing-preview-tab"><GitBranch size={13} aria-hidden="true"/>Duck Tape <span>/ workspace</span></div>
    </div>
    <div className="landing-preview-viewport">
      <iframe title="Application preview" src="/dashboard?preview=1" loading="lazy" tabIndex={-1} inert onLoad={event => {
        const doc = event.currentTarget.contentDocument;
        if (!doc || doc.getElementById('landing-preview-theme')) return;
        const style = doc.createElement('style');
        style.id = 'landing-preview-theme';
        style.textContent = previewTheme;
        doc.head.append(style);
        doc.documentElement.classList.add('landing-demo');
      }}/>
    </div>
  </div>;
}
