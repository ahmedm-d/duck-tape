import React,{createContext,useContext,useEffect,useState} from 'react';
import {createClient,type Session,type SupabaseClient} from '@supabase/supabase-js';
import {LogIn,LogOut} from 'lucide-react';
import {setAuthReady,setSessionToken,UNAUTHORIZED_EVENT} from './api';
import {previewMode} from './demo';
import './auth.css';

// Supabase Auth owns accounts and passwords. Signing in is optional: the workspace stays open, and the
// sign-in/sign-up screens appear only at /login and /signup. Only when the server runs with
// STEWARD_AUTH_REQUIRED=1 ("required") are signed-out visitors sent to /login; the server enforces that itself.
type AuthConfig={enabled:boolean;required?:boolean;url?:string;publishable_key?:string};
type Mode='login'|'signup';
type AuthState={enabled:boolean;email:string|null;signedIn:boolean;signOut:()=>Promise<void>};
const AuthContext=createContext<AuthState>({enabled:false,email:null,signedIn:false,signOut:async()=>{}});
export function useAuth(){return useContext(AuthContext)}
const AUTH_PATHS:Record<string,Mode>={'/login':'login','/signup':'signup'};
const NEXT_KEY='duck-tape:after-sign-in';

async function loadConfig():Promise<AuthConfig>{
 try{const res=await fetch('/api/auth/config');const body=res.ok?await res.json():null;
  return body?.enabled===true&&typeof body.url==='string'&&typeof body.publishable_key==='string'?body:{enabled:false}}
 catch{return {enabled:false}}
}
// Remember where the visitor was, so signing in returns them there.
export function rememberPage(){try{if(!AUTH_PATHS[location.pathname]&&location.pathname!=='/')sessionStorage.setItem(NEXT_KEY,location.pathname+location.search)}catch{}}
function destination(consume=true){let next='';try{next=sessionStorage.getItem(NEXT_KEY)||'';if(consume)sessionStorage.removeItem(NEXT_KEY)}catch{}
 return next.startsWith('/')&&!next.startsWith('//')&&!AUTH_PATHS[next.split('?')[0]]?next:'/dashboard'}

// Started at module load, before any component effect runs, so the workspace can render immediately
// (it must be mounted to see back/forward navigation) while API calls wait for the token.
type Loaded={config:AuthConfig;client:SupabaseClient|null};
const loaded:Promise<Loaded>=(previewMode?Promise.resolve<AuthConfig>({enabled:false}):loadConfig()).then(config=>{
 if(!config.enabled)return {config,client:null};
 const client=createClient(config.url!,config.publishable_key!,{auth:{persistSession:true,autoRefreshToken:true,detectSessionInUrl:true,flowType:'pkce'}});
 setSessionToken(async()=>(await client.auth.getSession()).data.session?.access_token);
 return {config,client}});
setAuthReady(loaded);

export function AuthGate({children}:{children:React.ReactNode}){
 const [config,setConfig]=useState<AuthConfig|null>(null);
 const [client,setClient]=useState<SupabaseClient|null>(null);
 const [session,setSession]=useState<Session|null|undefined>(undefined);
 const authMode=AUTH_PATHS[location.pathname],required=!!config?.required;
 useEffect(()=>{let active=true;loaded.then(value=>{if(!active)return;setClient(value.client);setConfig(value.config);if(!value.client)setSession(null)});
  return()=>{active=false}},[]);
 useEffect(()=>{if(!client)return;client.auth.getSession().then(({data})=>setSession(data.session));
  const {data}=client.auth.onAuthStateChange((_event,value)=>setSession(value));
  return()=>data.subscription.unsubscribe()},[client]);
 useEffect(()=>{if(!required)return;const expired=()=>{rememberPage();location.assign('/login')};
  addEventListener(UNAUTHORIZED_EVENT,expired);return()=>removeEventListener(UNAUTHORIZED_EVENT,expired)},[required]);
 // The workspace reads its route once at load, so leaving an auth page is a full navigation.
 const leave=!!config&&!!authMode&&(!config.enabled||!!session);
 const mustSignIn=required&&session===null&&!authMode&&location.pathname!=='/';
 useEffect(()=>{if(leave)location.replace(destination());else if(mustSignIn){rememberPage();location.replace('/login')}},[leave,mustSignIn]);
 if(authMode)return !config||leave||session===undefined?null:<AuthPage client={client!} initial={authMode} required={required}/>;
 if(mustSignIn)return null;
 const state:AuthState={enabled:!!config?.enabled,email:session?.user.email??null,signedIn:!!session,
  signOut:async()=>{await client?.auth.signOut();if(required)location.assign('/login')}};
 return <AuthContext.Provider value={state}>{children}</AuthContext.Provider>;
}

function AuthPage({client,initial,required}:{client:SupabaseClient;initial:Mode;required:boolean}){
 const [mode,setMode]=useState<Mode>(initial),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const signup=mode==='signup';
 function switchMode(next:Mode){setMode(next);setError('');setNotice('');history.replaceState(null,'','/'+next)}
 async function submit(e:React.FormEvent<HTMLFormElement>){e.preventDefault();setBusy(true);setError('');setNotice('');
  const form=new FormData(e.currentTarget),email=String(form.get('email')||'').trim(),password=String(form.get('password')||'');
  try{
   if(signup){
    if(password!==String(form.get('confirm')||''))throw new Error('Passwords do not match.');
    const {data,error}=await client.auth.signUp({email,password,options:{emailRedirectTo:location.origin+'/dashboard'}});
    if(error)throw error;
    // With "Confirm email" on, Supabase returns the user but no session until the link is opened.
    if(!data.session)setNotice('Account created. Open the confirmation link sent to '+email+', then sign in.');
   }else{const {error}=await client.auth.signInWithPassword({email,password});if(error)throw error}
  }catch(err){setError(err instanceof Error?err.message:String(err))}
  finally{setBusy(false)}}
 return <div className="auth-page"><div className="auth-card">
  <a className="auth-brand" href="/">Duck Tape</a>
  <h1>{signup?'Create your account':'Sign in'}</h1>
  <p className="muted">{signup?'Accounts are managed by Supabase. Duck Tape never sees your password.':'Sign in to the shared team workspace.'}</p>
  <p className="muted">All members share the same projects. Job actions still use labelled demo authors.</p>
  {error&&<div role="alert" className="alert error">{error}</div>}
  {notice&&<div role="status" className="alert">{notice}</div>}
  <form onSubmit={submit}>
   <label>Email<input name="email" type="email" autoComplete="email" required autoFocus/></label>
   <label>Password<input name="password" type="password" autoComplete={signup?'new-password':'current-password'} minLength={signup?8:undefined} required/></label>
   {signup&&<label>Confirm password<input name="confirm" type="password" autoComplete="new-password" minLength={8} required/></label>}
   <button className="primary" disabled={busy}>{busy?'Please wait…':signup?'Create account':'Sign in'}</button>
  </form>
  <p className="auth-switch">{signup?<>Already have an account? <button type="button" className="text-button" onClick={()=>switchMode('login')}>Sign in</button></>
   :<>New to Duck Tape? <button type="button" className="text-button" onClick={()=>switchMode('signup')}>Create an account</button></>}</p>
  {!required&&<p className="auth-switch"><a href={destination(false)}>Continue without an account</a></p>}
 </div></div>;
}

export function AccountMenu(){
 const auth=useAuth();
 if(!auth.enabled)return null;
 if(!auth.signedIn)return <a className="button account-sign-in" href="/login" onClick={rememberPage}><LogIn size={15}/>Sign in</a>;
 return <div className="account-menu"><span title={auth.email||''}>{auth.email||'Signed in'}</span>
  <button className="icon-button" aria-label="Sign out" title="Sign out" onClick={()=>auth.signOut()}><LogOut size={16}/></button></div>;
}
