from pathlib import Path
p=Path('C:/Users/ahmed/Projects/ducktape/steward/.worktrees/full-product/docs/evidence/installed-browser/acceptance.mjs');s=p.read_text(encoding='utf-8-sig')
s=s.replace("const baseline={repos:","const existing=JSON.parse(await fs.readFile('fresh-sample.json','utf8'));\nconst baseline={repos:")
s=s.replace("baseline.details=", "baseline.repos=baseline.repos.filter(r=>r.id!==existing.id);baseline.jobs=baseline.jobs.filter(j=>j.repo_id!==existing.id);baseline.knowledge=baseline.knowledge.filter(k=>k.repo_id!==existing.id);\nbaseline.details=")
s=s.replace("const repo=await api('/sample/reset',{});", "const repo=await api('/repositories/'+existing.id);")
s=s.replace("if(/\\/(ask|summarize|summary|summaries)(?:\\?|$)/.test(url))", "if(route.request().method()!=='GET'&&/\\/(ask|summarize|summary|summaries)(?:\\?|$)/.test(url))")
p.write_text(s,encoding='utf-8')
