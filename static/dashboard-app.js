(function(){
  const {$,toast,esc,escAttr,trim,formatDate,debounce,workspaceNavQuery,buildAppUrl,ensureDefaultWorkspace,FileManager,WorkspaceRename,bindSidebar,updateFilesBtnLabel,renderAiReason,normalizeChangeVerdict,publicReportFilename,isCleared,reviewBelongsToSide,reviewComments,renderChangeAiReason,renderChangeAiDetail}=PdfShared;
  let DOC_ID=null, RUN_ID=null, SNAPSHOT_ID=null, WORKSPACE_TITLE='Workspace';
  let SESSIONS=[], DATA=null, REVIEWS=[], AI_ASSESSMENT={items:[]}, CHANGE_AI_ASSESSMENT={items:[]};
  let CHANGE_BUCKETS={high:[],medium:[],low:[],na:[]}, REPORTS={};
  let activeSessionId=null, activeRisk=null, activeReportSide='new';
  let activeStatusFilter='total', activeOriginNew=false, activeOriginForward=false;
  let snapshotLoadGeneration=0;

  function dashboardSessionKey(){
    if(!DOC_ID)return null;
    if(SNAPSHOT_ID)return `${DOC_ID}:snap:${SNAPSHOT_ID}`;
    return `${DOC_ID}:idle`;
  }

  function releaseDashboardState(){
    DATA=null;
    REVIEWS=[];
    AI_ASSESSMENT={items:[]};
    CHANGE_AI_ASSESSMENT={items:[]};
    CHANGE_BUCKETS={high:[],medium:[],low:[],na:[]};
    REPORTS={};
    activeSessionId=null;
    RUN_ID=null;
    SNAPSHOT_ID=null;
    activeRisk=null;
    activeReportSide='new';
    activeStatusFilter='total';
    activeOriginNew=false;
    activeOriginForward=false;
  }
  window.addEventListener('pagehide',()=>releaseDashboardState());

  function formatDashDate(s){return formatDate(s,'—')}
  function navQuery(){return workspaceNavQuery({docId:DOC_ID,runId:RUN_ID,snapshotId:SNAPSHOT_ID})}
  function viewerLink(extra={}){const q=navQuery();Object.entries(extra).forEach(([k,v])=>{if(v!=null&&v!=='')q.set(k,String(v))});const qs=q.toString();return '/?'+(qs||'')}
  function activeSession(){return SESSIONS.find(s=>s.id===activeSessionId)||null}
  function isDiff(){return DATA?.mode==='diff'}
  function snapshotApiBase(){return `/api/documents/${DOC_ID}/snapshots/${SNAPSHOT_ID}`}
  function reviewText(rv){const cs=reviewComments(rv);return cs.length?cs[cs.length-1].text:(rv.comment||'(No comment)')}
  function changeAiItemForId(id){return (CHANGE_AI_ASSESSMENT.items||[]).find(x=>Number(x.change_id)===Number(id))}
  function aiItemForReview(id){return (AI_ASSESSMENT.items||[]).find(x=>x.review_id===id)}

  function updateWorkspaceTitle(){const el=$('#workspaceTitle');if(el)el.textContent=WORKSPACE_TITLE||'Workspace';updateFilesBtnLabel(DOC_ID)}
  function syncUrl(){
    if(!DOC_ID)return;
    const q=new URLSearchParams();
    q.set('doc',DOC_ID);
    if(SNAPSHOT_ID)q.set('snapshot',SNAPSHOT_ID);
    history.replaceState(null,'','/dashboard?'+q.toString());
  }

  function indexTypeLabel(it){
    if(!it)return 'SECTION';
    if(it.type==='fs')return 'FINANCIAL STATEMENT';
    if(it.type==='note')return 'NOTE';
    return String(it.type||'SECTION').toUpperCase();
  }
  function indexItemForPage(indexItems,page,side){
    if(!page||!indexItems?.length)return null;
    let best=null,bestPage=-1;
    for(const it of indexItems){
      const p=side==='old'?(it.old_page??it.page):it.page;
      if(p==null)continue;
      if(p<=page&&p>=bestPage){best=it;bestPage=p}
    }
    return best;
  }
  function indexHeadingForChange(c){
    const page=c.new_page||c.old_page;
    const it=indexItemForPage(DATA?.index_items||[],page,'new');
    const label=it?(it.label||it.title||indexTypeLabel(it)):('');
    const title=it?(it.title||it.label||''):('');
    return {label,title,indexItem:it};
  }
  function changeSectionFor(c){
    const page=c.new_page||c.old_page||'-';
    const {label,title,indexItem}=indexHeadingForChange(c);
    const type=indexTypeLabel(indexItem);
    const head=title||label||'Change';
    return `${type} 쨌 p.${page} 쨌 ${head}`;
  }
  function changeSummaryHtml(c){
    const parts=[];
    if(c.old)parts.push('<span class="delText">− '+esc(trim(c.old))+'</span>');
    if(c.new)parts.push('<span class="addText">+ '+esc(trim(c.new))+'</span>');
    return parts.join(' ')||'(empty)';
  }
  function indexHeadingHtml(c){
    const {label,title}=indexHeadingForChange(c);
    if(!label)return '';
    if(!title)return '<span class="indexLabel">'+esc(label)+'</span>';
    return '<span class="indexLabel">'+esc(label)+'</span> 쨌 <span class="indexTitle">'+esc(title)+'</span>';
  }

  function buildChangeViewModels(){
    const changes=DATA?.changes||[];
    const buckets={high:[],medium:[],low:[],na:[]};
    for(const raw of changes){
      const ai=changeAiItemForId(raw.id);
      const assessed=!!(ai&&ai.status==='done'&&String(ai.verdict||'').trim());
      const risk=assessed?normalizeChangeVerdict(ai.verdict):'na';
      const vm={
        id:Number(raw.id),
        old:raw.old_text||'',
        new:raw.new_text||'',
        ai:assessed?(ai.reasoning||ai.recommended_comment||''):null,
        indexLabel:indexHeadingForChange(raw).label,
        indexTitle:indexHeadingForChange(raw).title,
        section:changeSectionFor(raw),
        raw
      };
      buckets[risk].push(vm);
    }
    CHANGE_BUCKETS=buckets;
  }

  function buildReviewViewModels(side){
    return (REVIEWS||[]).filter(rv=>reviewBelongsToSide(rv,side)).map(rv=>{
      const ai=aiItemForReview(rv.review_id);
      const page=(side==='old'?rv.old_page:rv.new_page)||rv.page||(side==='old'?rv.old_anchor?.page:rv.new_anchor?.page);
      const it=indexItemForPage(DATA?.index_items||[],page,side);
      return {
        id:rv.review_id,
        text:reviewText(rv),
        status:isCleared(rv)?'cleared':'open',
        origin:rv.copied_from_review_id?'forward':'new',
        ai:ai&&(ai.reasoning||ai.verdict)?String(ai.reasoning||ai.verdict):null,
        indexLabel:it?(it.label||it.title||''):'',
        indexTitle:it?(it.title||it.label||''):'',
        raw:rv
      };
    });
  }
  function reviewBucketForSide(side){
    const all=buildReviewViewModels(side);
    return {total:all,open:all.filter(r=>r.status==='open'),cleared:all.filter(r=>r.status==='cleared')};
  }

  function buildSessionsFromSnapshots(snapshots){
    return [...(snapshots||[])].sort((a,b)=>String(b.created_at||'').localeCompare(String(a.created_at||''))).map(snap=>{
      const fn=publicReportFilename(snap.filename,'report.pdf');
      const compared=!!(snap.has_diff||snap.mode==='diff');
      const customLabel=String(snap.label||'').trim();
      const stamp=formatDashDate(snap.created_at);
      const label=customLabel||`${fn} 쨌 ${stamp}`;
      return {
        id:snap.snapshot_id,
        snapshot_id:snap.snapshot_id,
        source_run_id:snap.source_run_id||snap.run_id||null,
        label,
        created:stamp,
        updated:stamp,
        compared,
        add:0,del:0,changes:0,
        open_reviews:snap.open_reviews||0,
        closed_reviews:snap.closed_reviews||0,
        snapshot:snap
      };
    });
  }

  function rebuildReports(){
    if(!DATA){REPORTS={};return}
    const snap=activeSession()?.snapshot||{};
    const savedAt=formatDashDate(snap.created_at||activeSession()?.created);
    const newFile=publicReportFilename(snap.filename,publicReportFilename(DATA?.new_filename||DATA?.filename,'report.pdf'));
    if(isDiff()){
      const oldFile=publicReportFilename(DATA?.old_filename,'previous.pdf');
      REPORTS={
        old:{side:'old',tag:'Previous',name:oldFile,file:oldFile,pages:DATA.old_page_count||0,modified:savedAt},
        new:{side:'new',tag:'Current',name:newFile,file:newFile,pages:DATA.new_page_count||DATA.page_count||0,modified:savedAt}
      };
    }else{
      REPORTS={
        new:{side:'new',tag:'Current',name:newFile,file:newFile,pages:DATA.new_page_count||DATA.page_count||0,modified:savedAt}
      };
      activeReportSide='new';
    }
  }

  async function loadSnapshotData(snapshotId){
    const session=SESSIONS.find(s=>s.id===snapshotId);
    if(!session){toast('Session not found');return}
    const switchingSnapshot=activeSessionId!==snapshotId;
    const loadGen=++snapshotLoadGeneration;
    try{
      SNAPSHOT_ID=snapshotId;
      RUN_ID=session.source_run_id||session.snapshot?.source_run_id||session.snapshot?.run_id||null;
      const sessionKey=dashboardSessionKey();
      syncUrl();
      const base=snapshotApiBase();
      const [snapRes,reviews,assess,changeAssess]=await Promise.all([
        fetch(base).then(r=>{if(!r.ok)throw new Error('snapshot not found');return r.json()}),
        fetch(`${base}/reviews`).then(r=>r.ok?r.json():[]),
        fetch(`${base}/assess`).then(r=>r.ok?r.json():{items:[]}),
        fetch(`${base}/change-assess`).then(r=>r.ok?r.json():{items:[]})
      ]);
      if(loadGen!==snapshotLoadGeneration||sessionKey!==dashboardSessionKey())return;
      DATA=snapRes.result;
      REVIEWS=reviews||[];
      AI_ASSESSMENT=assess||{items:[]};
      CHANGE_AI_ASSESSMENT=changeAssess||{items:[]};
      session.compared=DATA?.mode==='diff';
      session.changes=(DATA?.changes||[]).length;
      session.add=DATA?.summary?.added||0;
      session.del=DATA?.summary?.deleted||0;
      buildChangeViewModels();
      rebuildReports();
      activeSessionId=snapshotId;
      if(switchingSnapshot){
        activeStatusFilter='total';
        activeOriginNew=false;
        activeOriginForward=false;
      }
      if(session.compared){
        if(switchingSnapshot)activeRisk=loadStoredRisk(DOC_ID,snapshotId)||'high';
      }else{
        activeRisk=null;
      }
      renderAll();
    }catch(e){
      toast('Failed to load session');
    }
  }

  function storedRiskKey(docId,snapId){return `pdfparser.dashboard.activeRisk.${docId}.${snapId}`}
  function loadStoredRisk(docId,snapId){
    try{
      const v=localStorage.getItem(storedRiskKey(docId,snapId));
      return v&&['high','medium','low','na'].includes(v)?v:null;
    }catch(e){return null}
  }
  function saveStoredRisk(){
    try{
      if(!DOC_ID||!SNAPSHOT_ID)return;
      const k=storedRiskKey(DOC_ID,SNAPSHOT_ID);
      if(activeRisk)localStorage.setItem(k,activeRisk);
      else localStorage.removeItem(k);
    }catch(e){}
  }

  async function openWorkspace(docId,snapshotId){
    DOC_ID=docId;
    releaseDashboardState();
    const meta=await fetch(`/api/documents/${docId}`).then(r=>{if(!r.ok)throw new Error('workspace not found');return r.json()});
    WORKSPACE_TITLE=meta.title||'Workspace';
    updateWorkspaceTitle();
    SESSIONS=buildSessionsFromSnapshots(meta.snapshots||[]);
    renderSessionList();
    if(snapshotId)await loadSnapshotData(snapshotId);
    else{
      renderEmptyDashboard();
      syncUrl();
    }
  }

  function renderEmptyDashboard(){
    $('#sessionLabel').textContent='Select a session';
    $('#sessionMetaBar').innerHTML='';
    $('#changeTotal').textContent='0';
    renderRiskCounts();
    renderChangeStream();
    renderReviewStream();
  }

  function sessionEmptyMessage(){
    return DOC_ID
      ? 'No snapshots yet. Save one from History in the viewer.'
      : 'Open a workspace to begin.';
  }

  function renderAll(){
    renderSessionList();
    selectSessionUi(activeSessionId);
    renderRiskCounts();
    renderChangeStats();
    renderReportPicker();
    selectReportSide(activeReportSide);
    renderChangeStream();
    renderReviewStream();
  }

  async function reloadWorkspaceSessions(){
    if(!DOC_ID)return;
    try{
      const meta=await fetch(`/api/documents/${DOC_ID}`).then(r=>{if(!r.ok)throw new Error('workspace not found');return r.json()});
      WORKSPACE_TITLE=meta.title||'Workspace';
      updateWorkspaceTitle();
      SESSIONS=buildSessionsFromSnapshots(meta.snapshots||[]);
      const keepId=activeSessionId&&SESSIONS.some(s=>s.id===activeSessionId)?activeSessionId:null;
      renderSessionList();
      if(keepId)await loadSnapshotData(keepId);
      else renderEmptyDashboard();
    }catch(e){toast('Failed to refresh sessions')}
  }

  async function deleteSession(snapshotId){
    if(!DOC_ID||!snapshotId)return;
    if(!confirm('Delete this session snapshot?'))return;
    try{
      const res=await fetch(`/api/documents/${DOC_ID}/snapshots/${snapshotId}`,{method:'DELETE'});
      const data=await res.json();
      if(!res.ok||data.error){toast(data.error||'Delete failed');return}
      if(activeSessionId===snapshotId)activeSessionId=null;
      await reloadWorkspaceSessions();
      toast('Session deleted');
    }catch(e){toast('Delete failed')}
  }

  function renderSessionList(){
    const pane=$('#sessionListPane'),countEl=$('#sessionListCount');
    if(countEl)countEl.textContent=String(SESSIONS.length);
    if(!pane)return;
    if(!SESSIONS.length){pane.innerHTML='<div class="empty">'+esc(sessionEmptyMessage())+'</div>';return}
    pane.innerHTML=SESSIONS.map(s=>{
      const active=s.id===activeSessionId?' active':'';
      return `<div class="sessionListRow${active}" data-session-id="${escAttr(s.id)}"><div class="meta">${esc(s.updated)}</div><div class="title">${esc(s.label)}</div><div class="sessionListActions"><button type="button" class="sessionDeleteBtn" data-delete-session="${escAttr(s.id)}">Delete</button></div></div>`;
    }).join('');
    pane.querySelectorAll('.sessionListRow').forEach(row=>{
      row.addEventListener('click',()=>loadSnapshotData(row.dataset.sessionId));
    });
    pane.querySelectorAll('[data-delete-session]').forEach(btn=>{
      btn.addEventListener('click',e=>{
        e.stopPropagation();
        deleteSession(btn.dataset.deleteSession);
      });
    });
  }

  function selectSessionUi(id){
    const s=SESSIONS.find(x=>x.id===id);
    if(!s)return;
    $('#sessionLabel').textContent=s.label;
    $('#sessionMetaBar').innerHTML=
      `<span><span class="k">saved_at</span> <span class="v">${esc(s.created)}</span></span>`+
      `<span><span class="k">reviews</span> <span class="v">${esc(String(s.open_reviews||0))} open / ${esc(String(s.closed_reviews||0))} closed</span></span>`;
  }

  function renderRiskCounts(){
    document.querySelectorAll('.riskCard').forEach(el=>{
      const k=el.dataset.risk;
      const num=el.querySelector('.num');
      if(num)num.textContent=String((CHANGE_BUCKETS[k]||[]).length);
    });
  }

  function renderChangeStats(){
    const s=SESSIONS.find(x=>x.id===activeSessionId);
    const statsLine=$('#changesStatsLine'),deltaEl=$('#changeDelta'),totalEl=$('#changeTotal');
    if(!s||!totalEl)return;
    totalEl.textContent=String(s.changes||0);
    if(s.compared){
      statsLine.classList.remove('muted');
      deltaEl.style.display='';
      deltaEl.innerHTML=`<span class="add"><span class="statLabel">add</span>+${s.add||0}</span><span class="del"><span class="statLabel">del</span>−${s.del||0}</span>`;
    }else{
      statsLine.classList.add('muted');
      deltaEl.style.display='none';
    }
  }

  function changeItemHtml(c,compact){
    const aiItem=changeAiItemForId(c.id);
    const ai=aiItem&&(aiItem.reasoning||aiItem.verdict)?`<div class="streamAi">${renderChangeAiReason(c.id,aiItem,{trim:160})}</div>`:`<div class="streamAi empty">No AI</div>`;
    const summary=`<div class="streamSummary">${changeSummaryHtml(c)}</div>`;
    const head=indexHeadingHtml(c);
    return `<div class="streamItem" data-change-id="${c.id}"><div class="streamMain"><div class="changeSection">${esc(c.section||'')}</div><div class="streamMeta">#${c.id}${head?' 쨌 '+head:''}</div>${summary}</div>${compact?ai:''}</div>`;
  }
  function changeModalItemHtml(c){
    const ai=changeAiItemForId(c.id);
    return `<div class="changeModalItem"><div class="changeSection">${esc(c.section||'')}</div><div class="streamMeta">#${c.id}${indexHeadingHtml(c)?' 쨌 '+indexHeadingHtml(c):''}</div><div class="changeModalSection"><strong>Change</strong><div class="changeModalDiff">${changeSummaryHtml(c)}</div></div>${renderChangeAiDetail(ai)}</div>`;
  }

  function renderChangeStream(){
    const stream=$('#changeStream'),body=$('#changeStreamBody');
    const session=SESSIONS.find(s=>s.id===activeSessionId);
    document.querySelectorAll('.riskCard').forEach(el=>el.classList.toggle('active',el.dataset.risk===activeRisk));
    if(!body||!stream)return;
    if(!session||!session.compared||!activeRisk){stream.classList.remove('show');body.innerHTML='';return}
    const items=CHANGE_BUCKETS[activeRisk]||[];
    stream.classList.add('show');
    if(!items.length){body.innerHTML='<div style="padding:12px;color:#94a3b8">No items.</div>';return}
    body.innerHTML=items.map(c=>changeItemHtml(c,true)).join('');
    body.querySelectorAll('.streamItem').forEach(el=>{
      el.addEventListener('click',()=>{
        const c=items.find(x=>x.id===Number(el.dataset.changeId));
        if(c)openChangeModal(c);
      });
    });
  }

  function openChangeModal(c){
    const ai=changeAiItemForId(c.id);
    const verdictMap={high:'High',medium:'Medium',low:'Low'};
    const verdict=ai?normalizeChangeVerdict(ai.verdict):null;
    $('#changeModalTitle').textContent='Change #'+c.id+(verdict?` - ${verdictMap[verdict]||'Low'}`:'');
    $('#changeModalBody').innerHTML=changeModalItemHtml(c);
    $('#changeListModal').classList.add('open');
  }

  function renderReportPicker(){
    const menu=$('#reportPickerMenu');
    if(!menu)return;
    const keys=Object.keys(REPORTS);
    menu.innerHTML=keys.map(key=>{
      const r=REPORTS[key];
      const active=r.side===activeReportSide?' active':'';
      return `<div class="reportPickerItem${active}" data-side="${escAttr(r.side)}"><div class="pickTag">${esc(r.tag)}</div><div class="pickName">${esc(r.name)}</div></div>`;
    }).join('');
    menu.querySelectorAll('.reportPickerItem').forEach(item=>{
      item.addEventListener('click',()=>{
        selectReportSide(item.dataset.side);
        menu.classList.remove('open');
        $('#reportPickerChev').textContent='▼';
        $('#reportPickerBtn').setAttribute('aria-expanded','false');
      });
    });
  }

  function selectReportSide(side){
    if(!REPORTS[side])return;
    activeReportSide=side;
    const r=REPORTS[side];
    $('#reportPickerTag').textContent=r.tag;
    $('#reportPickerName').textContent=r.name;
    $('#reportPickerMeta').textContent=(r.modified||'—')+' 쨌 '+(r.pages||0)+' pages';
    renderReportPicker();
    updateReviewStats();
    renderReviewStream();
  }

  function reviewIndexHeadingHtml(r){
    if(!r.indexLabel&&!r.indexTitle)return '';
    if(!r.indexTitle)return '<span class="indexLabel">'+esc(r.indexLabel)+'</span>';
    return '<span class="indexLabel">'+esc(r.indexLabel)+'</span> 쨌 <span class="indexTitle">'+esc(r.indexTitle)+'</span>';
  }
  function reviewOriginBadge(r){
    if(!r.origin)return '';
    const label=r.origin==='forward'?'forward':'new';
    return `<span class="rvBadge ${label}">${label}</span>`;
  }
  function reviewMetaHtml(r){
    const head=reviewIndexHeadingHtml(r);
    return `<span>${esc(r.status)}</span>${head?'<span class="metaSep">쨌</span>'+head:''}${reviewOriginBadge(r)}`;
  }

  function filterReviewsByOrigin(items){
    if(!activeOriginNew&&!activeOriginForward)return items;
    return items.filter(r=>(activeOriginNew&&r.origin==='new')||(activeOriginForward&&r.origin==='forward'));
  }
  function updateOriginFilterUi(){
    document.querySelectorAll('#reviewOriginBar .originFilter').forEach(btn=>{
      const kind=btn.dataset.origin;
      btn.classList.remove('active');
      if(kind==='all'&&!activeOriginNew&&!activeOriginForward)btn.classList.add('active');
      if(kind==='new'&&activeOriginNew)btn.classList.add('active');
      if(kind==='forward'&&activeOriginForward)btn.classList.add('active');
    });
  }
  function updateReviewStats(){
    const bucket=reviewBucketForSide(activeReportSide);
    const stats=document.querySelectorAll('#reviewStatsRow .reviewStat');
    if(stats[0])stats[0].querySelector('.n').textContent=String(bucket.total.length);
    if(stats[1])stats[1].querySelector('.n').textContent=String(bucket.cleared.length);
    if(stats[2])stats[2].querySelector('.n').textContent=String(bucket.open.length);
    stats.forEach(btn=>btn.classList.toggle('active',btn.dataset.filter===activeStatusFilter));
  }

  function renderReviewStream(){
    const body=$('#reviewStreamBody');
    $('#reviewStream').classList.add('show');
    updateOriginFilterUi();
    updateReviewStats();
    const bucket=reviewBucketForSide(activeReportSide);
    const baseItems=bucket[activeStatusFilter]||bucket.total||[];
    const items=filterReviewsByOrigin(baseItems);
    if(!items.length){body.innerHTML='<div style="padding:12px;color:#94a3b8">No comments match.</div>';return}
    body.innerHTML=items.map(r=>{
      const aiItem=aiItemForReview(r.id);
      const ai=aiItem&&(aiItem.reasoning||aiItem.verdict)?`<div class="streamAi">${renderAiReason(aiItem,{trim:160})}</div>`:`<div class="streamAi empty">No AI</div>`;
      const clearedCls=r.status==='cleared'?' cleared':'';
      return `<div class="streamItem${clearedCls}" data-review-id="${escAttr(r.id)}"><div class="streamMain"><div class="streamMeta">${reviewMetaHtml(r)}</div><div class="streamSummary">${esc(trim(r.text))}</div></div>${ai}</div>`;
    }).join('');
    body.querySelectorAll('.streamItem').forEach(el=>{
      el.addEventListener('click',()=>{
        const r=items.find(x=>x.id===el.dataset.reviewId);
        if(r)openReviewModal(r);
      });
    });
  }

  function openReviewModal(r){
    const ai=aiItemForReview(r.id);
    $('#reviewModalTitle').textContent='Comment '+r.id;
    $('#reviewModalBody').innerHTML=
      `<div class="changeModalSection"><strong>Status</strong><div>${esc(r.status||'open')}</div></div>`+
      `<div class="changeModalSection"><strong>Comment</strong><div>${esc(r.text)}</div></div>`+
      (ai?`<div class="changeModalSection"><strong>AI opinion</strong>${renderAiReason(ai)}</div>`:'')+
      `<div class="streamMeta" style="margin-top:8px">${reviewMetaHtml(r)}</div>`;
    $('#reviewDetailModal').classList.add('open');
  }

  WorkspaceRename.init({
    canStart:()=>!!DOC_ID,
    getTitle:()=>WORKSPACE_TITLE,
    onCommit:async next=>{
      if(!DOC_ID)return;
      const res=await fetch(`/api/file-manager/${DOC_ID}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:next})});
      const data=await res.json();
      if(!res.ok){toast(data.error==='duplicate title'?'Name already exists':(data.error||'Rename failed'));return}
      WORKSPACE_TITLE=next;
      toast('Renamed');
    },
    onFinish:()=>updateWorkspaceTitle(),
  });
  FileManager.init({
    getCtx:()=>({docId:DOC_ID}),
    onOpenWorkspace:async docId=>{FileManager.close();try{await openWorkspace(docId,null)}catch(e){toast('Failed to open workspace')}},
    onRenamed:async (docId,title)=>{if(DOC_ID===docId){WORKSPACE_TITLE=title;updateWorkspaceTitle()}},
    onDeleted:async docId=>{
      if(DOC_ID!==docId)return;
      try{
        const ws=await ensureDefaultWorkspace();
        await openWorkspace(ws.workspace_id,null);
      }catch(e){DOC_ID=null;updateWorkspaceTitle();renderEmptyDashboard();renderSessionList()}
    },
  });
  bindSidebar();
  $('#workspaceHomeBtn').addEventListener('click',()=>{if(!DOC_ID){toast('Open a workspace first.');return}location.href=viewerLink()});
  $('#workspaceAnalysisBtn').addEventListener('click',()=>{if(!DOC_ID){toast('Open a workspace first.');return}location.href=viewerLink()});
  $('#workspaceFilesBtn').addEventListener('click',()=>FileManager.open('manage'));
  $('#workspaceRenameBtn').addEventListener('click',()=>WorkspaceRename.start());
  $('#riskGrid').addEventListener('click',e=>{
    const card=e.target.closest('.riskCard');if(!card)return;
    activeRisk=activeRisk===card.dataset.risk?null:card.dataset.risk;
    saveStoredRisk();
    renderChangeStream();
  });
  $('#reportPickerBtn').addEventListener('click',()=>{
    const menu=$('#reportPickerMenu'),open=menu.classList.toggle('open');
    $('#reportPickerChev').textContent=open?'▲':'▼';
    $('#reportPickerBtn').setAttribute('aria-expanded',open?'true':'false');
  });
  document.addEventListener('click',e=>{
    const menu=$('#reportPickerMenu');if(!menu.classList.contains('open'))return;
    if(!e.target.closest('.reportPicker')){menu.classList.remove('open');$('#reportPickerChev').textContent='▼';$('#reportPickerBtn').setAttribute('aria-expanded','false')}
  });
  $('#reviewStatsRow').addEventListener('click',e=>{
    const btn=e.target.closest('.reviewStat');if(!btn?.dataset.filter)return;
    activeStatusFilter=btn.dataset.filter;renderReviewStream();
  });
  document.querySelectorAll('#reviewOriginBar .originFilter').forEach(btn=>{
    btn.addEventListener('click',()=>{
      const kind=btn.dataset.origin;
      if(kind==='all'){activeOriginNew=false;activeOriginForward=false}
      else if(kind==='new')activeOriginNew=!activeOriginNew;
      else if(kind==='forward')activeOriginForward=!activeOriginForward;
      renderReviewStream();
    });
  });
  ['changeModalClose','changeListModal'].forEach(id=>{
    const el=$(id==='changeListModal'?'#changeListModal':'#'+id);
    if(!el)return;
    if(id==='changeListModal')el.addEventListener('click',e=>{if(e.target===el)el.classList.remove('open')});
    else el.addEventListener('click',()=>$('#changeListModal').classList.remove('open'));
  });
  ['reviewModalClose','reviewDetailModal'].forEach(id=>{
    const el=$('#'+id);if(!el)return;
    if(id==='reviewDetailModal')el.addEventListener('click',e=>{if(e.target===el)el.classList.remove('open')});
    else el.addEventListener('click',()=>$('#reviewDetailModal').classList.remove('open'));
  });

  const reloadWorkspaceSessionsDebounced=debounce(async()=>{
    if(!DOC_ID)return;
    try{
      const meta=await fetch(`/api/documents/${DOC_ID}`).then(r=>{if(!r.ok)throw new Error('workspace not found');return r.json()});
      WORKSPACE_TITLE=meta.title||'Workspace';
      updateWorkspaceTitle();
      SESSIONS=buildSessionsFromSnapshots(meta.snapshots||[]);
      const keepId=activeSessionId&&SESSIONS.some(s=>s.id===activeSessionId)?activeSessionId:null;
      renderSessionList();
      if(keepId)await loadSnapshotData(keepId);
      else renderEmptyDashboard();
    }catch(e){toast('Failed to refresh sessions')}
  },400);

  document.addEventListener('visibilitychange',()=>{
    if(document.visibilityState==='visible'&&DOC_ID)reloadWorkspaceSessionsDebounced();
  });
  async function bootstrap(){
    const params=new URLSearchParams(location.search);
    const docId=params.get('doc');
    const snapshotId=params.get('snapshot');
    if(docId){
      try{await openWorkspace(docId,snapshotId||null)}catch(e){toast('Failed to load workspace')}
      return;
    }
    try{
      const ws=await ensureDefaultWorkspace();
      await openWorkspace(ws.workspace_id,null);
    }catch(e){toast('Failed to open workspace')}
  }
  bootstrap();
})();
