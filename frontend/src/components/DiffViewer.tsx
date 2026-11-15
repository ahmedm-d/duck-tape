import hljs from 'highlight.js/lib/core';
import python from 'highlight.js/lib/languages/python';
import javascript from 'highlight.js/lib/languages/javascript';
import typescript from 'highlight.js/lib/languages/typescript';
import json from 'highlight.js/lib/languages/json';
import css from 'highlight.js/lib/languages/css';
import xml from 'highlight.js/lib/languages/xml';
import {useId,useLayoutEffect,useRef,useState} from 'react';
hljs.registerLanguage('python',python);hljs.registerLanguage('javascript',javascript);hljs.registerLanguage('typescript',typescript);hljs.registerLanguage('json',json);hljs.registerLanguage('css',css);hljs.registerLanguage('xml',xml);
const languages:Record<string,string>={py:'python',js:'javascript',jsx:'javascript',mjs:'javascript',cjs:'javascript',ts:'typescript',tsx:'typescript',json:'json',css:'css',html:'xml',svg:'xml'};
function Code({text,path}:{text:string,path:string}){
 const language=languages[path.split('.').at(-1)||''];
 return language?<code dangerouslySetInnerHTML={{__html:hljs.highlight(text||' ',{language,ignoreIllegals:true}).value}}/>:<code>{text||' '}</code>;
}
type ChangedFile={path:string;before?:string|null;after?:string|null;op?:string;old_path?:string;destination?:string;binary?:boolean;before_hash?:string|null;hash?:string|null};
type LineChanges={before:Set<number>;after:Set<number>};
type SplitLine={text:string;number:number;changed:boolean};
export function alignedLines(before:string|null|undefined,after:string|null|undefined,changes?:LineChanges) {
 const lines=(text:string|null|undefined)=>text?text.replace(/\n$/,'').split('\n'):[];
 const oldLines=lines(before),newLines=lines(after),rows:{before:SplitLine|null;after:SplitLine|null}[]=[];
 let left=0,right=0;
 const take=(side:'before'|'after'):SplitLine=>{const index=side==='before'?left++:right++;return {text:(side==='before'?oldLines:newLines)[index],number:index+1,changed:changes?.[side].has(index+1)||false}};
 while(left<oldLines.length||right<newLines.length){
  if(changes?.before.has(left+1)||changes?.after.has(right+1)){
   const removed:SplitLine[]=[],added:SplitLine[]=[];
   while(left<oldLines.length&&changes?.before.has(left+1))removed.push(take('before'));
   while(right<newLines.length&&changes?.after.has(right+1))added.push(take('after'));
   if(removed.length||added.length){for(let index=0;index<Math.max(removed.length,added.length);index++)rows.push({before:removed[index]||null,after:added[index]||null});continue}
  }
  rows.push({before:left<oldLines.length?take('before'):null,after:right<newLines.length?take('after'):null});
 }
 return rows;
}

function SplitDiff({file,changes}:{file:ChangedFile;changes?:LineChanges}) {
 const root=useRef<HTMLDivElement>(null);
 // Ignore scroll events caused by synchronization so they cannot overwrite newer user input.
 const synchronized=useRef(new WeakMap<HTMLPreElement,number>());
 const move=(pane:HTMLPreElement,offset:number)=>{if(pane.scrollLeft!==offset){pane.scrollLeft=offset;synchronized.current.set(pane,pane.scrollLeft)}};
 const rows=file.binary?[]:alignedLines(file.before,file.after,changes);
 useLayoutEffect(()=>{
  const panes=Array.from(root.current!.querySelectorAll('pre'));
  const resize=()=>{
   const offset=panes[0].scrollLeft;
   const bodies=panes.map(pane=>pane.querySelector<HTMLElement>('.split-lines')!);
   bodies.forEach(body=>body.style.width='max-content');
   const overflow=Math.max(0,...bodies.map((body,index)=>body.scrollWidth-panes[index].clientWidth));
   // Equal scroll ranges prevent the shorter pane from clamping and pulling its peer back.
   bodies.forEach((body,index)=>body.style.width=(panes[index].clientWidth+overflow)+'px');
   panes.forEach(pane=>move(pane,offset));
  };
  resize();const observer=new ResizeObserver(resize);observer.observe(root.current!);
  document.fonts.addEventListener('loadingdone',resize);
  return ()=>{observer.disconnect();document.fonts.removeEventListener('loadingdone',resize)};
 },[file.before,file.after,file.binary,changes]);
 return <div className="split-diff" ref={root}>{(['before','after'] as const).map(side=><div key={side}><div className="diff-side-label">{side==='before'?'Before':'After'}</div><pre onScroll={event=>{const source=event.currentTarget,expected=synchronized.current.get(source);synchronized.current.delete(source);if(expected!==undefined&&Math.abs(source.scrollLeft-expected)<0.5)return;root.current?.querySelectorAll('pre').forEach(pane=>{if(pane!==source)move(pane,source.scrollLeft)})}}><div className="split-lines">{file.binary?<div className="diff-placeholder">Binary content. Inspect the exported patch or files archive.</div>:rows.map((row,index)=>{const line=row[side],tone=line?.changed?(side==='before'?'deletion':'addition'):'';return <div className={'diff-code-line '+(line?tone:'diff-gap')} key={index} aria-label={line?undefined:'No corresponding line'}><span className="line-number">{line?.number}</span><span className="diff-sign">{line?.changed?(side==='before'?'-':'+'):' '}</span>{line&&<Code text={line.text} path={file.path}/>}</div>})}</div></pre></div>)}</div>;
}
export function changedLines(diff:string) {
 const files=new Map<string,{before:Set<number>;after:Set<number>}>();
 let beforePath='',afterPath='',oldLine=0,newLine=0,oldRemaining=0,newRemaining=0;
 let changes:{before:Set<number>;after:Set<number>}|undefined;
 const path=(header:string)=>{let value=header.split('\t')[0];if(value.startsWith('"')){try{value=JSON.parse(value)}catch{/* Keep an unrecognised quoted path unmatched. */}}return value.replace(/^[ab]\//,'')};
 for(const line of diff.split('\n')) {
  const hunk=/^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/.exec(line);
  if(hunk){oldLine=Number(hunk[1]);oldRemaining=Number(hunk[2]??1);newLine=Number(hunk[3]);newRemaining=Number(hunk[4]??1);continue}
  if(changes&&(oldRemaining>0||newRemaining>0)) {
   if(line.startsWith('-')&&oldRemaining>0){changes.before.add(oldLine++);oldRemaining--;continue}
   if(line.startsWith('+')&&newRemaining>0){changes.after.add(newLine++);newRemaining--;continue}
   if(line.startsWith(' ')){oldLine++;newLine++;oldRemaining--;newRemaining--;continue}
   if(line.startsWith('\\'))continue;
  }
  if(line.startsWith('diff --git ')){oldRemaining=0;newRemaining=0;changes=undefined}
  else if(line.startsWith('--- ')){beforePath=path(line.slice(4));changes=undefined}
  else if(line.startsWith('+++ ')){
   afterPath=path(line.slice(4));changes={before:new Set(),after:new Set()};
   if(beforePath!=='/dev/null')files.set(beforePath,changes);
   if(afterPath!=='/dev/null')files.set(afterPath,changes);
  }
 }
 return files;
}
function FullFile({file,onClose}:{file:ChangedFile;onClose:()=>void}) {
 const dialog=useRef<HTMLDialogElement>(null),[version,setVersion]=useState<'before'|'after'>(file.after==null?'before':'after');
 useLayoutEffect(()=>{const previous=document.activeElement as HTMLElement|null;dialog.current?.showModal();return()=>{dialog.current?.close();previous?.focus()}},[]);
 const content=file[version];
 return <dialog className="full-file-dialog" ref={dialog} aria-label={'Full file: '+file.path} onClose={event=>{if(!event.currentTarget.open)onClose()}} onClick={event=>{if(event.target===event.currentTarget)onClose()}}>
  <div className="section-head"><h2>{file.path}</h2><button autoFocus onClick={onClose}>Close file</button></div>
  <div className="toolbar"><select aria-label="File version" value={version} onChange={event=>setVersion(event.target.value as 'before'|'after')}><option value="before">Before</option><option value="after">After</option></select><span className="muted">Captured revision content</span></div>
  {file.binary?<p>Binary file. Download the changed files archive to inspect it.</p>:content==null?<p>This version has no captured file content.</p>:content===''?<p>Empty file</p>:<pre className="full-file-code"><Code text={content} path={file.path}/></pre>}
 </dialog>;
}
export default function DiffViewer({files,diff,split}:{files:ChangedFile[];diff:string;split:boolean}){
 const prefix=useId().replace(/:/g,'');
 const [selected,setSelected]=useState(''),[fullFile,setFullFile]=useState<ChangedFile|null>(null);
 const selectedFile=files.find(file=>file.path===selected)||files[0];
 const changes=split?changedLines(diff):new Map();
 let currentPath='',oldLine=0,newLine=0;
 const operation=(file:ChangedFile)=>file.op==='rename'||file.old_path||file.destination?'Renamed':file.before_hash===null&&!!file.hash||!file.binary&&file.before==null||diff.includes('--- /dev/null\n+++ b/'+file.path)?'Added':file.hash===null&&!!file.before_hash||!file.binary&&file.after==null||diff.includes('--- a/'+file.path+'\n+++ /dev/null')?'Deleted':'Modified';
 return <section className="diff-shell" aria-label="Patch diff">
  <nav className="diff-files" aria-label="Changed files">{files.map(file=><a href={'#'+prefix+'-'+encodeURIComponent(file.destination||file.path)} onClick={event=>{event.preventDefault();setSelected(file.path);document.getElementById(prefix+'-'+encodeURIComponent(file.destination||file.path))?.scrollIntoView({block:'nearest'})}} key={file.path}><span>{file.path}<small>{operation(file)}{file.binary?' | Binary':''}</small></span></a>)}</nav>
  <div className="diff-content">{selectedFile&&<div className="full-file-toolbar"><select aria-label="File to view" value={selectedFile.path} onChange={event=>setSelected(event.target.value)}>{files.map(file=><option key={file.path}>{file.path}</option>)}</select><button onClick={()=>setFullFile(selectedFile)}>View full file</button></div>}{split?files.map(file=><section key={file.path} id={prefix+'-'+encodeURIComponent(file.destination||file.path)}><div className="diff-heading">{file.old_path?file.old_path+' → ':''}{file.path}<span>{operation(file)}</span></div><SplitDiff file={file} changes={changes.get(file.destination||file.path)}/></section>):<pre className="unified">{(diff||'No textual changes').split('\n').map((line,index)=>{
   let className='',before='',after='',id:string|undefined;
   if(line.startsWith('diff --git ')){currentPath=line.split(' b/').slice(1).join(' b/');id=prefix+'-'+encodeURIComponent(currentPath);className='diff-meta'}
   else if(line.startsWith('@@')){const hunk=/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);if(hunk){oldLine=Number(hunk[1]);newLine=Number(hunk[2])}className='hunk'}
   else if(line.startsWith('---')||line.startsWith('+++')||line.startsWith('index ')||line.startsWith('rename ')||line.startsWith('new file')||line.startsWith('deleted file'))className='diff-meta';
   else if(line.startsWith('+')){className='addition';after=String(newLine++)}
   else if(line.startsWith('-')){className='deletion';before=String(oldLine++)}
   else if(line.startsWith(' ')){before=String(oldLine++);after=String(newLine++)}
   else className='diff-meta';
   return <div key={index} id={id} className={'diff-code-line '+className}><span className="line-number">{before}</span><span className="line-number">{after}</span><span className="diff-sign">{className==='addition'?'+':className==='deletion'?'-':' '}</span>{['addition','deletion',''].includes(className)?<Code text={line.slice(1)} path={currentPath}/>:<code>{line||' '}</code>}</div>
  })}</pre>}</div>
 {fullFile&&<FullFile file={fullFile} onClose={()=>setFullFile(null)}/>}</section>;
}
