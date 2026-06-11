(function(global){
  const $=s=>document.querySelector(s);

  function toast(msg){
    const t=$('#toast');
    if(!t)return;
    t.textContent=msg;
    t.classList.add('show');
    clearTimeout(t._t);
    t._t=setTimeout(()=>t.classList.remove('show'),2800);
  }

  function esc(s){
    return String(s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }
  function escAttr(s){return esc(s)}
  function trim(s,n=160){s=String(s||'');return s.length>n?s.slice(0,n-1)+'...':s}
  function formatDate(s,empty='-'){
    if(!s)return empty;
    return String(s).replace('T',' ').slice(0,19)||empty;
  }
  function titleFromFilename(name){return String(name||'Workspace').replace(/\.pdf$/i,'')}

  function workspaceNavQuery(ctx){
    const q=new URLSearchParams();
    if(ctx.docId)q.set('doc',ctx.docId);
    if(ctx.snapshotId)q.set('snapshot',ctx.snapshotId);
    else if(ctx.runId)q.set('run',ctx.runId);
    return q;
  }

  function buildAppUrl(page,ctx){
    const q=workspaceNavQuery(ctx);
    const qs=q.toString();
    if(page==='dashboard')return '/dashboard'+(qs?'?'+qs:'');
    return '/?'+(qs||'');
  }

  function debounce(fn,ms){
    let timer=null;
    return (...args)=>{clearTimeout(timer);timer=setTimeout(()=>fn(...args),ms);};
  }

  const INTERNAL_PDF_NAMES=new Set(['source.pdf','report.pdf','prev_report.pdf','old.pdf','new.pdf','']);
  function publicReportFilename(name,fallback='report.pdf'){
    const n=String(name||'').trim();
    return INTERNAL_PDF_NAMES.has(n.toLowerCase())?(fallback||'report.pdf'):n;
  }

  function reviewComments(rv){return Array.isArray(rv?.comments)?rv.comments:[]}
  function reviewBelongsToSide(rv,side){
    return side==='old'
      ?((rv?.old_word_ids||[]).length>0||rv?.side==='old')
      :((rv?.new_word_ids||[]).length>0||rv?.side==='new');
  }

  function renderChangeAiReason(item,opts={}){
    if(!item||!(item.reasoning||item.verdict))return '';
    const verdict=normalizeChangeVerdict(item.verdict);
    const verdictMap={high:'High',medium:'Medium',low:'Low'};
    let reasoningText=String(item.reasoning||'').replace(/\s+/g,' ').trim();
    if(opts.trim&&reasoningText.length>opts.trim)reasoningText=reasoningText.slice(0,opts.trim).trim()+'...';
    const rec=verdict==='low'?'':String(item.recommended_comment||'').trim();
    const recLine=rec?`<div><strong>Suggested review:</strong> ${esc(opts.trim?trim(rec,opts.trim):rec)}</div>`:'';
    const forwarded=item.forwarded_review_id?`<div><strong>Forwarded:</strong> ${esc(item.forwarded_review_id)}</div>`:'';
    return `<div class="aiReason"><div class="riskBadge"><span class="riskDot ${esc(verdict)}"></span><strong>${esc(verdictMap[verdict]||'Low')}</strong></div>${reasoningText?`<div>${esc(reasoningText)}</div>`:''}${recLine}${forwarded}</div>`;
  }

  function renderChangeAiDetail(item){
    if(!item||!(item.reasoning||item.verdict))return '<div class="changeModalSection"><strong>AI opinion</strong><div style="color:#94a3b8;font-size:11px">No AI</div></div>';
    const verdict=normalizeChangeVerdict(item.verdict);
    const verdictMap={high:'High',medium:'Medium',low:'Low'};
    const reason=String(item.reasoning||'').trim();
    const rec=verdict==='low'?'':String(item.recommended_comment||'').trim();
    let html=`<div class="changeModalSection"><strong>AI opinion</strong><div class="aiReason"><div class="riskBadge"><span class="riskDot ${esc(verdict)}"></span><strong>${esc(verdictMap[verdict]||'Low')}</strong></div>${reason?`<div>${esc(reason)}</div>`:''}</div></div>`;
    if(rec)html+=`<div class="changeModalSection"><strong>Suggested review</strong><div>${esc(rec)}</div></div>`;
    if(item.forwarded_review_id)html+=`<div class="changeModalSection"><strong>Forwarded review</strong><div>${esc(item.forwarded_review_id)}</div></div>`;
    return html;
  }

  const Modal={
    open(el){if(!el)return;el.classList.add('open');if(el.classList.contains('changeModalBackdrop')||el.classList.contains('reviewModalBackdrop'))el.style.display='flex';},
    close(el){if(!el)return;el.classList.remove('open');if(el.classList.contains('changeModalBackdrop')||el.classList.contains('reviewModalBackdrop'))el.style.display='none';},
  };

  function normalizeChangeVerdict(v){
    const raw=String(v||'').toLowerCase().trim();
    if(raw==='risk')return 'medium';
    if(raw==='no_risk')return 'low';
    if(raw==='unclear'||raw==='unknown')return 'low';
    if(raw==='high'||raw==='medium'||raw==='low')return raw;
    return 'low';
  }

  function verdictLabel(value){return String(value||'unclear').replace(/_/g,' ').toUpperCase()}

  function renderAiReason(item,opts={}){
    if(!item||!(item.reasoning||item.verdict))return '';
    const modelVerdict=item.verdict||'unclear';
    const finalVerdict=item.human_decision||modelVerdict;
    const verdictLine=`<strong>Verdict: ${esc(verdictLabel(finalVerdict))}</strong>`;
    let reasoningText=String(item.reasoning||'');
    if(String(finalVerdict).toLowerCase()==='unclear'){
      reasoningText=reasoningText.replace(/\s+/g,' ').trim();
      if(reasoningText.length>140)reasoningText=reasoningText.slice(0,140).trim()+'...';
    }else if(opts.trim){
      reasoningText=trim(reasoningText,opts.trim);
    }
    const reasonLine=reasoningText?`<div>${esc(reasoningText)}</div>`:'';
    return `<div class="aiReason">${verdictLine}${reasonLine}</div>`;
  }

  function isCleared(rv){
    const s=rv?.status||'';
    return s==='cleared'||s==='resolved'||s==='closed';
  }

  async function ensureDefaultWorkspace(){
    const res=await fetch('/api/workspace/default');
    const data=await res.json();
    if(!res.ok||!data.workspace_id)throw new Error(data.error||'Failed to ensure workspace');
    return data;
  }

  const FileManager={
    mode:'manage',
    browseWorkspaceId:null,
    browseWorkspaceTitle:'',
    hooks:{},
    bindOnce:false,
    renderGeneration:0,
    init(hooks){
      this.hooks=hooks||{};
      if(this.bindOnce)return;
      this.bindOnce=true;
      const dlg=$('#fmDialog');
      if(dlg){
        dlg.addEventListener('click',async e=>{
          const viewBtn=e.target.closest('button[data-fm-view]');
          if(viewBtn&&viewBtn.dataset.fmView==='workspaces'){
            this.browseWorkspaceId=null;
            this.browseWorkspaceTitle='';
            await this.render();
            return;
          }
        });
      }
      const wrap=$('#fmTableWrap');
      if(!wrap)return;
      wrap.addEventListener('click',async e=>{
        const btn=e.target.closest('button[data-fm-action]');
        if(!btn)return;
        const action=btn.dataset.fmAction;
        const docId=btn.dataset.docId||'';
        const runId=btn.dataset.runId||'';
        if(action==='open'){
          const item=(window.__fmItems||[]).find(x=>x.workspace_id===docId&&(x.file_id||x.run_id||x.latest_run_id)===runId);
          if(item&&this.hooks.onOpenFile)await this.hooks.onOpenFile(item);
          return;
        }
        if(action==='select'){
          const item=(window.__fmItems||[]).find(x=>x.workspace_id===docId&&(x.file_id||x.run_id||x.latest_run_id)===runId);
          if(item&&this.hooks.onSelectFile)await this.hooks.onSelectFile(item);
          return;
        }
        if(action==='browse'){
          const item=(window.__fmItems||[]).find(x=>x.workspace_id===docId);
          this.browseWorkspaceId=docId;
          this.browseWorkspaceTitle=item?.title||item?.name||'Workspace';
          await this.render();
          return;
        }
        if(action==='switch'){
          this.close();
          if(this.hooks.onOpenWorkspace)await this.hooks.onOpenWorkspace(docId);
          return;
        }
        if(this.mode==='upload')return;
        if(action==='rename')await this.renameWorkspace(docId);
        else if(action==='delete')await this.deleteWorkspace(docId);
        else if(action==='rename-file')await this.renameFile(docId,runId);
        else if(action==='delete-file')await this.deleteFile(docId,runId);
        else if(action==='export')this.exportFile(docId,runId);
      });
      const closeBtn=$('#fmCloseBtn');
      if(closeBtn)closeBtn.addEventListener('click',()=>this.close());
    },
    close(){
      const dlg=$('#fmDialog');
      if(dlg)dlg.style.display='none';
      this.browseWorkspaceId=null;
      this.browseWorkspaceTitle='';
    },
    activePanel(){
      // Single-workspace app: the manager only ever lists the current workspace's saved files.
      return 'files';
    },
    renderHead(_ctx,_panel){
      const titleEl=$('#fmDialogTitle');
      if(!titleEl)return;
      const label=esc(this.browseWorkspaceTitle||'Workspace');
      titleEl.innerHTML=`<div class="fmHeadTitle"><strong>${label} — Saved files</strong></div>`;
    },
    async resolveBrowseTarget(ctx){
      let docId=String(ctx?.docId||'').trim();
      let title=String(ctx?.workspaceTitle||'').trim();
      if(!docId){
        try{
          const res=await fetch('/api/file-manager');
          const data=await res.json();
          const items=data.items||[];
          if(items.length===1){
            docId=items[0].workspace_id||'';
            title=items[0].title||items[0].name||'';
          }
        }catch(e){}
      }
      if(docId&&!title){
        try{
          const meta=await fetch(`/api/documents/${docId}`).then(r=>r.ok?r.json():null);
          title=meta?.title||'';
        }catch(e){}
      }
      return {docId,title:title||'Workspace'};
    },
    async open(mode){
      const dlg=$('#fmDialog');
      if(!dlg)return;
      this.mode=mode||'manage';
      this.browseWorkspaceId=null;
      this.browseWorkspaceTitle='';
      const ctx=this.hooks.getCtx?this.hooks.getCtx():{};
      const target=await this.resolveBrowseTarget(ctx);
      if(target.docId){
        this.browseWorkspaceId=target.docId;
        this.browseWorkspaceTitle=target.title;
      }
      dlg.style.display='flex';
      await this.render();
    },
    fileCount(item){
      return item.file_count??0;
    },
    fileRenameDefault(item){
      const onDisk=String(item?.filename||item?.latest_filename||item?.name||'').trim();
      if(onDisk)return titleFromFilename(onDisk);
      return String(item?.title||'File').trim()||'File';
    },
    filesWorkspaceId(){
      if(this.browseWorkspaceId)return this.browseWorkspaceId;
      const ctx=this.hooks.getCtx?this.hooks.getCtx():{};
      return String(ctx?.docId||'').trim()||null;
    },
    async renderFiles(){
      const wrap=$('#fmTableWrap');
      const docId=this.filesWorkspaceId();
      if(!wrap||!docId)return false;
      const generation=++this.renderGeneration;
      const res=await fetch(`/api/documents/${docId}/files`);
      if(generation!==this.renderGeneration)return true;
      const data=await res.json();
      const files=data.files||[];
      const items=files.map(file=>({
        workspace_id:docId,
        file_id:file.file_id||file.run_id,
        run_id:file.run_id||file.file_id,
        latest_run_id:file.run_id||file.file_id,
        title:file.title,
        filename:file.filename,
        latest_filename:file.filename,
        name:file.filename,
        updated_at:file.updated_at,
        created_at:file.created_at,
        latest_status:file.status||'ready',
      }));
      window.__fmItems=items;
      if(!items.length){
        wrap.innerHTML='<div class="empty">No saved files in this workspace yet. Use Save on a report pane.</div>';
        return true;
      }
      wrap.innerHTML=`<table class="fmTable"><thead><tr><th>Name</th><th>Saved</th><th>Status</th><th>Actions</th></tr></thead><tbody>${items.map(item=>{
        const display=esc(item.filename||item.latest_filename||item.title||'File');
        const subtitleTitle=esc(item.title||'');
        const name=subtitleTitle&&subtitleTitle!==display.replace(/\.pdf$/i,'')
          ?`${display}<div class="meta">${subtitleTitle}</div>`
          :display;
        const date=esc(formatDate(item.updated_at||item.created_at));
        const status=esc(item.latest_status||'-');
        const runId=escAttr(item.latest_run_id||'');
        const workspaceId=escAttr(item.workspace_id||'');
        const openBtn=`<button type="button" data-fm-action="open" data-doc-id="${workspaceId}" data-run-id="${runId}">Open</button>`;
        const useBtn=`<button type="button" data-fm-action="select" data-doc-id="${workspaceId}" data-run-id="${runId}">Select</button>`;
        const exportBtn=`<button type="button" data-fm-action="export" data-doc-id="${workspaceId}" data-run-id="${runId}">Export</button>`;
        const manageBtns=`<button type="button" data-fm-action="rename-file" data-doc-id="${workspaceId}" data-run-id="${runId}">Rename</button><button type="button" data-fm-action="delete-file" data-doc-id="${workspaceId}" data-run-id="${runId}">Delete</button>`;
        const missing=item.latest_status==='missing';
        const openAction=missing
          ? `<button type="button" disabled title="PDF file missing on disk">Open</button>`
          : openBtn;
        const useAction=missing
          ? `<button type="button" disabled title="PDF file missing on disk">Select</button>`
          : useBtn;
        // Upload mode is a single action: selecting a file uploads/uses it. No separate Open.
        const rowActions=this.mode==='upload'?`${useAction}`:`${openAction}${exportBtn}${manageBtns}`;
        return `<tr><td>${name}</td><td>${date}</td><td>${status}</td><td><div class="fmActions">${rowActions}</div></td></tr>`;
      }).join('')}</tbody></table>`;
      return true;
    },
    async renderWorkspaces(){
      const wrap=$('#fmTableWrap');
      if(!wrap)return false;
      const generation=++this.renderGeneration;
      const res=await fetch('/api/file-manager');
      if(generation!==this.renderGeneration)return true;
      const data=await res.json();
      const items=data.items||[];
      window.__fmItems=items;
      if(!items.length){
        wrap.innerHTML='<div class="empty">No saved workspaces yet.</div>';
        return true;
      }
      wrap.innerHTML=`<table class="fmTable"><thead><tr><th>Workspace</th><th>Updated</th><th>Files</th><th>Actions</th></tr></thead><tbody>${items.map(item=>{
        const title=esc(item.title||item.name||'Workspace');
        const date=esc(formatDate(item.updated_at||item.created_at));
        const workspaceId=escAttr(item.workspace_id||'');
        const browseBtn=`<button type="button" data-fm-action="browse" data-doc-id="${workspaceId}">Browse</button>`;
        const switchBtn=`<button type="button" data-fm-action="switch" data-doc-id="${workspaceId}">Switch</button>`;
        const manageBtns=`<button type="button" data-fm-action="rename" data-doc-id="${workspaceId}">Rename</button><button type="button" data-fm-action="delete" data-doc-id="${workspaceId}">Delete</button>`;
        const actions=this.mode==='upload'?browseBtn:`${browseBtn}${switchBtn}${manageBtns}`;
        return `<tr><td>${title}</td><td>${date}</td><td>${this.fileCount(item)}</td><td><div class="fmActions">${actions}</div></td></tr>`;
      }).join('')}</tbody></table>`;
      return true;
    },
    async render(){
      const wrap=$('#fmTableWrap');
      if(!wrap)return;
      const ctx=this.hooks.getCtx?this.hooks.getCtx():{};
      const panel=this.activePanel(ctx);
      wrap.innerHTML='<div class="empty">Loading...</div>';
      this.renderHead(ctx,panel);
      try{
        if(panel==='files'){
          if(!this.filesWorkspaceId()){
            wrap.innerHTML='<div class="empty">Select a workspace first.</div>';
            return;
          }
          await this.renderFiles();
          return;
        }
        await this.renderWorkspaces();
      }catch(e){
        wrap.innerHTML='<div class="empty">Failed to load.</div>';
      }
    },
    async renameWorkspace(docId){
      const item=(window.__fmItems||[]).find(x=>x.workspace_id===docId);
      if(!item)return;
      const title=prompt('Rename workspace', item.title||item.name||'Workspace');
      if(title===null)return;
      const next=String(title||'').trim();
      if(!next){toast('Name is required');return}
      const res=await fetch(`/api/file-manager/${docId}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:next})});
      const data=await res.json();
      if(!res.ok){toast(data.error==='duplicate title'?'Name already exists':(data.error||'Rename failed'));return}
      if(this.hooks.onRenamed)await this.hooks.onRenamed(docId,next);
      toast('Renamed');
      await this.render();
    },
    async deleteWorkspace(docId){
      if(!confirm('Delete this workspace?'))return;
      const res=await fetch(`/api/file-manager/${docId}`,{method:'DELETE'});
      const data=await res.json();
      if(!res.ok){toast(data.error||'Delete failed');return}
      if(this.hooks.onDeleted)await this.hooks.onDeleted(docId);
      toast('Deleted');
      await this.render();
    },
    async renameFile(docId,runId){
      const item=(window.__fmItems||[]).find(x=>x.workspace_id===docId&&(x.file_id||x.run_id||x.latest_run_id)===runId);
      if(!item)return;
      const current=this.fileRenameDefault(item);
      const next=window.prompt('Rename file',current);
      if(next==null)return;
      const title=String(next).trim();
      if(!title||title===current)return;
      const res=await fetch(`/api/documents/${docId}/files/${runId}`,{
        method:'PATCH',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({title}),
      });
      const data=await res.json();
      if(!res.ok){toast(data.error==='duplicate title'?'Name already exists':(data.error||'Rename failed'));return}
      toast('Renamed');
      await this.render();
    },
    async deleteFile(docId,runId){
      if(!confirm('Delete this saved file?'))return;
      const res=await fetch(`/api/documents/${docId}/files/${runId}`,{method:'DELETE'});
      const data=await res.json();
      if(!res.ok){toast(data.error||'Delete failed');return}
      toast('Deleted');
      await this.render();
    },
    exportFile(docId,runId){
      if(!docId||!runId){toast('No file to export');return}
      window.open(`/api/documents/${docId}/files/${runId}/report`,'_blank');
    },
  };

  const WorkspaceRename={
    input:null,
    original:null,
    hooks:{},
    init(hooks){
      this.hooks=hooks||{};
      if(this._bound)return;
      this._bound=true;
      document.addEventListener('mousedown',e=>{
        if(!this.input)return;
        if(!e.target.closest('#workspaceRenameBtn')&&e.target!==this.input)this.finish(true);
      });
    },
    resizeInput(input){
      const v=String(input?.value||'').trim()||'Workspace';
      input.style.width=Math.min(24,Math.max(8,v.length+1))+'ch';
    },
    async finish(commit=true){
      if(!this.input||!this.original)return;
      const input=this.input;
      const original=this.original;
      if(commit){
        const next=String(input.value||'').trim();
        if(next&&this.hooks.onCommit)await this.hooks.onCommit(next);
      }
      input.replaceWith(original);
      this.input=null;
      this.original=null;
      if(this.hooks.onFinish)this.hooks.onFinish();
    },
    start(){
      if(this.hooks.canStart&&!this.hooks.canStart())return;
      if(this.input){this.finish(true);return}
      const titleEl=$('#workspaceTitle');
      if(!titleEl)return;
      const input=document.createElement('input');
      input.type='text';
      input.className='sideTitleInput';
      input.value=(this.hooks.getTitle&&this.hooks.getTitle())||'Workspace';
      this.input=input;
      this.original=titleEl;
      this.resizeInput(input);
      titleEl.replaceWith(input);
      input.focus();
      input.select();
      input.addEventListener('input',()=>this.resizeInput(input));
      input.addEventListener('keydown',e=>{
        if(e.key==='Enter'){e.preventDefault();this.finish(true)}
        else if(e.key==='Escape'){e.preventDefault();this.finish(false)}
      });
    },
  };

  function bindSidebar(opts={}){
    const collapse=$('#sidebarCollapseBtn');
    const rail=$('#sidebarRail');
    if(collapse)collapse.addEventListener('click',()=>{
      document.body.classList.add('sidebarCollapsed');
      if(opts.onCollapse)opts.onCollapse();
    });
    if(rail)rail.addEventListener('click',()=>{
      document.body.classList.remove('sidebarCollapsed');
      if(opts.onExpand)opts.onExpand();
    });
  }

  function updateFilesBtnLabel(){
    const btn=$('#workspaceFilesBtn');
    if(!btn)return;
    btn.title='Files';
    btn.setAttribute('aria-label','Files');
  }

  global.PdfShared={
    $,toast,esc,escAttr,trim,formatDate,titleFromFilename,debounce,
    workspaceNavQuery,buildAppUrl,Modal,
    publicReportFilename,reviewComments,reviewBelongsToSide,
    renderChangeAiReason,renderChangeAiDetail,
    normalizeChangeVerdict,verdictLabel,renderAiReason,isCleared,
    ensureDefaultWorkspace,
    FileManager,WorkspaceRename,bindSidebar,updateFilesBtnLabel,
  };
})(window);
