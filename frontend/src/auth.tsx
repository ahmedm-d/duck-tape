import React,{createContext,useContext,useEffect,useState} from 'react';
import {createClient,type Session,type SupabaseClient} from '@supabase/supabase-js';
import {LogOut} from 'lucide-react';
import {setSessionToken,UNAUTHORIZED_EVENT} from './api';
import {previewMode} from './demo';
import './auth.css';

// Supabase Auth owns accounts and passwords; the backend verifies the access token on every /api request.
// This gate is only the user experience: the server enforces sign-in whether or not this renders.
type AuthConfig={enabled:boolean;url?:string;publishable_key?:string};
type Account={email:string|null;signOut:()=>Promise<void>};
type Mode='login'|'signup';
const AccountContext=createContext<Account|null>(null);
export function useAccount(){return useContext(AccountContext)}
const AUTH_PATHS:Record<string,Mode>={'/login':'login','/signup':'signup'};
const NEXT_KEY='duck-tape:after-sign-in';

async function loadConfig():Promise<AuthConfig>{
 try{const res=await fetch('/api/auth/config');const body=res.ok?await res.json():null;
  return body?.enabled===true&&typeof body.url==='string'&&typeof body.publishable_key==='string'?body:{enabled:false}}
 catch{return {enabled:false}}
}
function remember(path:string){try{sessionStorage.setItem(NEXT_KEY,path)}catch{}}
function destination(){let next='';try{next=sessionStorage.getItem(NEXT_KEY)||'';sessionStorage.removeItem(NEXT_KEY)}catch{}
 return next.startsWith('/')&&!next.startsWith('//')&&!AUTH_PATHS[next]?next:'/dashboard'}

export function AuthGate({children}:{children:React.ReactNode}){
 const [config,setConfig]=useState<AuthConfig|null>(previewMode?{enabled:false}:null);
 const [client,setClient]=useState<SupabaseClient|null>(null);
 const [session,setSession]=useState<Session|null|undefined>(undefined);
 const path=location.pathname,authMode=AUTH_PATHS[path];
 useEffect(()=>{if(previewMode)return;let active=true;loadConfig().then(value=>{if(!active)return;
  if(value.enabled){const supabase=createClient(value.url!,value.publishable_key!,{auth:{persistSession:true,autoRefreshToken:true,detectSessionInUrl:true,flowType:'pkce'}});
   setSessionToken(async()=>(await supabase.auth.getSession()).data.session?.access_token);setClient(supabase)}
  setConfig(value)});return()=>{active=false}},[]);
 useEffect(()=>{if(!client)return;client.auth.getSession().then(({data})=>setSession(data.session));
  const {data}=client.auth.onAuthStateChange((_event,value)=>setSession(value));
  const expired=()=>setSession(null);addEventListener(UNAUTHORIZED_EVENT,expired);
  return()=>{data.subscription.unsubscribe();removeEventListener(UNAUTHORIZED_EVENT,expired)}},[client]);
 // The workspace reads its route once at load, so leaving an auth page is a full navigation.
 const leaveAuthPage=!!authMode&&!!config&&(!config.enabled||!!session);
 useEffect(()=>{if(leaveAuthPage)location.replace(destination())},[leaveAuthPage]);
 if(!config||leaveAuthPage)return null;
 if(!config.enabled)return <>{children}</>;
 if(session===undefined)return null;
 if(!session){if(path==='/'&&!authMode)return <>{children}</>;
  if(!authMode)remember(path+location.search);
  return <AuthPage client={client!} initial={authMode||'login'}/>}
 const account:Account={email:session.user.email??null,signOut:async()=>{await client!.auth.signOut();location.assign('/login')}};
 return <AccountContext.Provider value={account}>{children}</AccountContext.Provider>;
}

function AuthPage({client,initial}:{client:SupabaseClient;initial:Mode}){
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
  <p className="muted">{signup?'Accounts are managed by Supabase. Duck Tape never sees your password.':'Sign in to open the team workspace.'}</p>
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
 </div></div>;
}

export function AccountMenu(){
 const account=useAccount();
 if(!account)return null;
 return <div className="account-menu"><span title={account.email||''}>{account.email||'Signed in'}</span>
  <button className="icon-button" aria-label="Sign out" title="Sign out" onClick={()=>account.signOut()}><LogOut size={16}/></button></div>;
}
