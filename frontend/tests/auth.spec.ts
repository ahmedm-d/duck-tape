import {test,expect,type Page} from '@playwright/test';
import {fixture} from './fixtures';

// Mocked transport only: Supabase Auth is a fake origin served by page.route; no live project is contacted.
const SUPABASE='https://example-ref.supabase.co';
async function signInEnabled(page:Page){
 const calls=await fixture(page),authorization:string[]=[];
 page.on('request',request=>{if(new URL(request.url()).pathname.startsWith('/api/')&&!request.url().includes('/api/auth/config'))authorization.push(request.headers()['authorization']||'')});
 await page.route('**/api/auth/config',route=>route.fulfill({json:{enabled:true,provider:'supabase',url:SUPABASE,publishable_key:'sb_publishable_fixture'}}));
 const now=Math.floor(Date.now()/1000),user={id:'user-1',aud:'authenticated',role:'authenticated',email:'dev@example.com',app_metadata:{},user_metadata:{},created_at:new Date().toISOString()};
 const session={access_token:'header.'+Buffer.from(JSON.stringify({sub:'user-1',exp:now+3600})).toString('base64url')+'.sig',token_type:'bearer',expires_in:3600,expires_at:now+3600,refresh_token:'refresh',user};
 const supabase:{path:string,body:any}[]=[];
 await page.route(SUPABASE+'/**',async route=>{const url=new URL(route.request().url());let body:any=null;try{body=route.request().postDataJSON()}catch{}
  supabase.push({path:url.pathname+url.search,body});
  if(url.pathname==='/auth/v1/token')return body?.password==='correct-horse'?route.fulfill({json:session}):route.fulfill({status:400,json:{error:'invalid_grant',error_description:'Invalid login credentials',msg:'Invalid login credentials',code:'invalid_credentials'}});
  if(url.pathname==='/auth/v1/signup')return route.fulfill({json:{...user,email:body?.email,confirmation_sent_at:new Date().toISOString()}});
  if(url.pathname==='/auth/v1/logout')return route.fulfill({status:204,body:''});
  return route.fulfill({status:404,json:{}})});
 return {calls,authorization,supabase};
}

test('workspace asks for a Supabase sign-in and sends the access token to the API',async({page})=>{
 const {authorization,supabase}=await signInEnabled(page);
 await page.goto('/dashboard');
 await expect(page.getByRole('heading',{name:'Sign in'})).toBeVisible();
 await page.getByLabel('Email').fill('dev@example.com');await page.getByLabel('Password').fill('wrong');
 await page.getByRole('button',{name:'Sign in'}).click();
 await expect(page.getByRole('alert')).toContainText('Invalid login credentials');
 await page.getByLabel('Password').fill('correct-horse');await page.getByRole('button',{name:'Sign in'}).click();
 await expect(page.getByRole('heading',{name:'Dashboard'})).toBeVisible();
 await expect(page.getByText('dev@example.com')).toBeVisible();
 expect(supabase.filter(call=>call.path.startsWith('/auth/v1/token')).map(call=>call.body.email)).toEqual(['dev@example.com','dev@example.com']);
 await expect.poll(()=>authorization.length).toBeGreaterThan(0);
 expect(authorization.every(value=>value.startsWith('Bearer header.'))).toBeTruthy();
 await page.getByRole('button',{name:'Sign out'}).click();
 await expect(page).toHaveURL(/\/login$/);
 await expect(page.getByRole('heading',{name:'Sign in'})).toBeVisible();
});

test('sign-up explains email confirmation and the landing page stays public',async({page})=>{
 const {supabase}=await signInEnabled(page);
 await page.goto('/');
 await page.getByRole('link',{name:'Create account'}).click();
 await expect(page.getByRole('heading',{name:'Create your account'})).toBeVisible();
 await page.getByLabel('Email').fill('new@example.com');
 await page.getByLabel('Password',{exact:true}).fill('longenough1');await page.getByLabel('Confirm password').fill('different1');
 await page.getByRole('button',{name:'Create account'}).click();
 await expect(page.getByRole('alert')).toContainText('Passwords do not match');
 await page.getByLabel('Confirm password').fill('longenough1');
 await page.getByRole('button',{name:'Create account'}).click();
 await expect(page.getByRole('status')).toContainText('confirmation link sent to new@example.com');
 expect(supabase.filter(call=>call.path.startsWith('/auth/v1/signup')).map(call=>call.body.email)).toEqual(['new@example.com']);
});

test('sign-in pages fall through to the workspace when the server has sign-in disabled',async({page})=>{
 await fixture(page);
 await page.goto('/login');
 await expect(page).toHaveURL(/\/dashboard$/);
 await expect(page.getByRole('heading',{name:'Dashboard'})).toBeVisible();
});
