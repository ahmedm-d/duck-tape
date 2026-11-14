import hljs from 'highlight.js/lib/core';
import python from 'highlight.js/lib/languages/python';
import javascript from 'highlight.js/lib/languages/javascript';
import typescript from 'highlight.js/lib/languages/typescript';
import json from 'highlight.js/lib/languages/json';
import css from 'highlight.js/lib/languages/css';
import xml from 'highlight.js/lib/languages/xml';
import {useId} from 'react';
hljs.registerLanguage('python',python);hljs.registerLanguage('javascript',javascript);hljs.registerLanguage('typescript',typescript);hljs.registerLanguage('json',json);hljs.registerLanguage('css',css);hljs.registerLanguage('xml',xml);
const languages:Record<string,string>={py:'python',js:'javascript',jsx:'javascript',mjs:'javascript',cjs:'javascript',ts:'typescript',tsx:'typescript',json:'json',css:'css',html:'xml',svg:'xml'};
function Code({text,path}:{text:string,path:string}){
 const language=languages[path.split('.').at(-1)||''];
 return language?<code dangerouslySetInnerHTML={{__html:hljs.highlight(text||' ',{language,ignoreIllegals:true}).value}}/>:<code>{text||' '}</code>;
}
type ChangedFile={path:string;before?:string|null;after?:string|null;op?:string;old_path?:string;destination?:string;binary?:boolean;before_hash?:string|null;hash?:string|null};
export default function DiffViewer({files,diff,split}:{files:ChangedFile[];diff:string;split:boolean}){
 const prefix=useId().replace(/:/g,'');
 let currentPath='',oldLine=0,newLine=0;
 const operation=(file:ChangedFile)=>file.op==='rename'||file.old_path||file.destination?'Renamed':file.before_hash===null&&!!file.hash||!file.binary&&file.before==null||diff.includes('--- /dev/null\n+++ b/'+file.path)?'Added':file.hash===null&&!!file.before_hash||!file.binary&&file.after==null||diff.includes('--- a/'+file.path+'\n+++ /dev/null')?'Deleted':'Modified';
 return <section className="diff-shell" aria-label="Patch diff">
  <nav className="diff-files" aria-label="Changed files">{files.map(file=><a href={'#'+prefix+'-'+encodeURIComponent(file.destination||file.path)} onClick={event=>{event.preventDefault();document.getElementById(prefix+'-'+encodeURIComponent(file.destination||file.path))?.scrollIntoView({block:'nearest'})}} key={file.path}><span>{file.path}<small>{operation(file)}{file.binary?' | Binary':''}</small></span></a>)}</nav>
  <div className="diff-content">{split?files.map(file=><section key={file.path} id={prefix+'-'+encodeURIComponent(file.destination||file.path)}><div className="diff-heading">{file.old_path?file.old_path+' → ':''}{file.path}<span>{operation(file)}</span></div><div className="split-diff">{[['Before',file.before],['After',file.after]].map(([label,content])=><div key={label}><div className="diff-side-label">{label}</div><pre>{file.binary?<div className="diff-placeholder">Binary content. Inspect the exported patch or files archive.</div>:content==null?<div className="diff-placeholder">{label==='Before'?'New file':'Deleted file'}</div>:content.split('\n').map((line,index)=><div className="diff-code-line" key={index}><span className="line-number">{index+1}</span><Code text={line} path={file.path}/></div>)}</pre></div>)}</div></section>):<pre className="unified">{(diff||'No textual changes').split('\n').map((line,index)=>{
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
 </section>;
}
