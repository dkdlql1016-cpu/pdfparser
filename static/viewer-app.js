const {$,toast,esc:escapeHtml,escAttr:escapeHtmlAttr,trim,formatDate,titleFromFilename,debounce,workspaceNavQuery,buildAppUrl,ensureDefaultWorkspace,FileManager,WorkspaceRename,bindSidebar,updateFilesBtnLabel,publicReportFilename,isCleared,reviewBelongsToSide,reviewComments,normalizeChangeVerdict,verdictLabel,renderAiReason,renderChangeAiDetail,Modal}=PdfShared;
let DATA=null, DOC_ID=null, RUN_ID=null, SNAPSHOT_ID=null, READ_ONLY=false;
let WORKSPACE_TITLE='Workspace';
let PREV_REPORT_FILENAME='';
let CUR_REPORT_FILENAME='';
let WORDS={old:[],new:[]};
let CHARS={old:[],new:[]};
let EQUAL={pairs:[],newToOld:new Map(),oldToNew:new Map(),newEqual:new Set(),oldEqual:new Set()};
let REVIEWS=[];
let AI_ASSESSMENT={items:[]};
let CHANGE_AI_ASSESSMENT={items:[]};
let CHANGE_FILTERS={ai:'all',risk:'all'};
let REVIEW_SIDE='new';
let REVIEW_FILTERS={status:'all',ai:'all',match:'all',sort:'default'};
let UPLOAD_CONTEXT='base';
let AI_RUN_STATE={running:false,total:0,done:0,failed:0,message:''};
let AI_RUN_STATUS_TIMER=null;
let CHANGE_AI_RUN_STATE={running:false,total:0,done:0,failed:0,message:''};
let CHANGE_AI_RUN_STATUS_TIMER=null;
let REVIEW_AI_ABORT_CONTROLLER=null;
let CHANGE_AI_ABORT_CONTROLLER=null;
let REVIEW_AI_RUNNING_IDS=new Set();
let CHANGE_AI_RUNNING_IDS=new Set();
let REVIEW_DETAIL_MODAL_ID=null;
let REVIEW_EDITING_COMMENT_ID=null;
let HOME_RETURN_COLLAPSED=false;
let BASE_UPLOAD_NAME='';
let UPDATE_UPLOAD_NAME='';
let PENDING_BASE_FILE=null;
let PENDING_UPDATE_FILE=null;
let PENDING_BASE_SOURCE_WORKSPACE_ID=null;
let ZOOM={old:1,new:1};
let ZOOM_CUSTOM={old:false,new:false};

function runSessionKey(){
  if(!DOC_ID)return null;
  if(SNAPSHOT_ID)return `${DOC_ID}:snap:${SNAPSHOT_ID}`;
  if(RUN_ID)return `${DOC_ID}:run:${RUN_ID}`;
  return `${DOC_ID}:idle`;
}
function clearViewerPages(){
  ['#oldPages','#newPages'].forEach(sel=>{
    const root=$(sel);
    if(!root)return;
    root.querySelectorAll('img').forEach(img=>{img.removeAttribute('src');img.src=''});
    root.innerHTML='';
  });
  clearReviewMarkers();
  clearPreview();
}
function abortAnalysisTasks(){
  if(REVIEW_AI_ABORT_CONTROLLER){REVIEW_AI_ABORT_CONTROLLER.abort();REVIEW_AI_ABORT_CONTROLLER=null}
  if(CHANGE_AI_ABORT_CONTROLLER){CHANGE_AI_ABORT_CONTROLLER.abort();CHANGE_AI_ABORT_CONTROLLER=null}
  if(AI_RUN_STATUS_TIMER){clearTimeout(AI_RUN_STATUS_TIMER);AI_RUN_STATUS_TIMER=null}
  if(CHANGE_AI_RUN_STATUS_TIMER){clearTimeout(CHANGE_AI_RUN_STATUS_TIMER);CHANGE_AI_RUN_STATUS_TIMER=null}
  REVIEW_AI_RUNNING_IDS=new Set();
  CHANGE_AI_RUNNING_IDS=new Set();
  updateAiButtons();
}
function releaseAnalysisSession(opts={}){
  const keepWorkspace=!!opts.keepWorkspace;
  abortAnalysisTasks();
  RUN_ID=null;
  SNAPSHOT_ID=null;
  DATA=null;
  READ_ONLY=false;
  if(!keepWorkspace){
    DOC_ID=null;
    WORKSPACE_TITLE='Workspace';
  }
  PREV_REPORT_FILENAME='';
  CUR_REPORT_FILENAME='';
  WORDS={old:[],new:[]};
  CHARS={old:[],new:[]};
  EQUAL={pairs:[],newToOld:new Map(),oldToNew:new Map(),newEqual:new Set(),oldEqual:new Set()};
  REVIEWS=[];
  AI_ASSESSMENT={items:[]};
  CHANGE_AI_ASSESSMENT={items:[]};
  PENDING_BASE_FILE=null;
  PENDING_UPDATE_FILE=null;
  PENDING_BASE_SOURCE_WORKSPACE_ID=null;
  BASE_UPLOAD_NAME='';
  UPDATE_UPLOAD_NAME='';
  clearViewerPages();
  closeReviewPopover();
  closeChangeAiModal();
  closeReviewDetailModal();
  if(opts.meta)syncUploadFormsFromMeta(opts.meta);
  else{
    $('#createForm').style.display='flex';
    $('#updateForm').style.display='none';
  }
  $('#viewer').classList.add('single');
  $('#indexList').innerHTML='<div class="empty">Upload a report to view the outline.</div>';
  $('#changes').innerHTML=opts.meta&&workspaceHasComparison(opts.meta)?'<div class="empty">Use Updated Report upload to start a new comparison.</div>':'<div class="empty">Upload an updated report to compare changes.</div>';
  $('#reviewPane').innerHTML='<div class="reviewPlaceholder">No comments yet.</div>';
  updateWorkspaceTitle();
  if(keepWorkspace)syncUrlFromState();
  setBusy(false,'Idle');
}
function resetRunSession(meta){
  releaseAnalysisSession({keepWorkspace:true,meta});
}
async function openWorkspaceSession(docId){
  const meta=await fetch(`/api/documents/${docId}`).then(r=>{if(!r.ok)throw new Error('workspace not found');return r.json()});
  DOC_ID=docId;
  WORKSPACE_TITLE=meta.title||'Workspace';
  updateWorkspaceTitle();
  resetRunSession(meta);
  await loadSnapshots();
  return meta;
}
window.addEventListener('pagehide',()=>{if(DOC_ID)history.replaceState(null,'','/?doc='+encodeURIComponent(DOC_ID))});

document.querySelectorAll('.tabBtn').forEach(btn=>btn.addEventListener('click',()=>activateTab(btn.dataset.tab)));
function updateWorkspaceTitle(){const el=$('#workspaceTitle');if(el)el.textContent=WORKSPACE_TITLE||'Workspace';updateWorkspaceNavButtons();updateFilesBtnLabel(DOC_ID)}
function navQuery(){return workspaceNavQuery({docId:DOC_ID,runId:RUN_ID,snapshotId:SNAPSHOT_ID})}
function syncUrlFromState(){
  if(!DOC_ID)return;
  const q=navQuery();
  const target='/?'+q.toString();
  if(location.pathname==='/'&&location.search==='?'+q.toString())return;
  history.replaceState(null,'',target);
}
function setSidebarCollapsed(collapsed){document.body.classList.toggle('sidebarCollapsed',collapsed);const rail=$('#sidebarRail');if(rail)rail.textContent='☰';if(DATA)requestAnimationFrame(()=>draw(DATA))}
bindSidebar({onExpand:()=>{if(DATA)requestAnimationFrame(()=>draw(DATA))}});
$('#workspaceHomeBtn').addEventListener('click',()=>openHomeView());
$('#workspaceDashboardBtn').addEventListener('click',()=>{
  if(!DOC_ID){toast('Open a workspace first.');return}
  location.href=buildAppUrl('dashboard',{docId:DOC_ID,runId:RUN_ID,snapshotId:SNAPSHOT_ID});
});
$('#workspaceAnalysisBtn').addEventListener('click',()=>{
  if(document.body.classList.contains('homeMode')){closeHomeView();return}
  if(!DOC_ID){toast('Open a workspace first.');return}
  const q=navQuery();
  const target='/?'+q.toString();
  if(location.pathname==='/'&&location.search==='?'+q.toString())return;
  location.href=target;
});
$('#workspaceFilesBtn').addEventListener('click',()=>FileManager.open('manage'));
$('#workspaceRenameBtn').addEventListener('click',()=>WorkspaceRename.start());
WorkspaceRename.init({
  getTitle:()=>WORKSPACE_TITLE,
  onCommit:async next=>{
    if(!DOC_ID){WORKSPACE_TITLE=next;return}
    const res=await fetch(`/api/file-manager/${DOC_ID}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:next})});
    const data=await res.json();
    if(!res.ok){toast(data.error==='duplicate title'?'Name already exists':(data.error||'Rename failed'));return}
    WORKSPACE_TITLE=next;
    toast('Renamed');
  },
  onFinish:()=>updateWorkspaceTitle(),
});
window.addEventListener('resize',debounce(()=>{if(DATA)draw(DATA)},150));
$('#baseUploadBtn').addEventListener('click',e=>{e.preventDefault();openSourcePicker('base',e.currentTarget)});
$('#updateUploadBtn').addEventListener('click',e=>{e.preventDefault();openSourcePicker('update',e.currentTarget)});
$('#initialFile').addEventListener('change',()=>{PENDING_BASE_FILE=null;PENDING_BASE_SOURCE_WORKSPACE_ID=null;BASE_UPLOAD_NAME=selectedInputName('#initialFile');updateUploadFileNames()});
$('#updateFile').addEventListener('change',()=>{PENDING_UPDATE_FILE=null;UPDATE_UPLOAD_NAME=selectedInputName('#updateFile');updateUploadFileNames()});
$('#sourceFileManagerBtn').addEventListener('click',()=>{closeSourcePicker();FileManager.open('upload')});
$('#sourceExternalBtn').addEventListener('click',()=>{const id=UPLOAD_CONTEXT==='base'?'#initialFile':'#updateFile';closeSourcePicker();$(id).click()});
document.addEventListener('click',e=>{const picker=$('#sourcePicker');if(!picker||picker.style.display==='none')return;if(picker.contains(e.target)||e.target.id==='baseUploadBtn'||e.target.id==='updateUploadBtn')return;closeSourcePicker()});
document.addEventListener('click',e=>{const modal=$('#changeAiModal');if(!modal||modal.style.display!=='flex')return;if(e.target===modal)closeChangeAiModal()});
document.addEventListener('click',e=>{const modal=$('#reviewDetailModal');if(!modal||modal.style.display!=='flex')return;if(e.target===modal)closeReviewDetailModal()});
function closeTopOverlayOnEscape(){
  const reviewModal=$('#reviewDetailModal');
  if(reviewModal&&reviewModal.style.display==='flex'){closeReviewDetailModal();return true}
  const changeModal=$('#changeAiModal');
  if(changeModal&&changeModal.style.display==='flex'){closeChangeAiModal();return true}
  const sourcePicker=$('#sourcePicker');
  if(sourcePicker&&sourcePicker.style.display!=='none'){closeSourcePicker();return true}
  const fmDialog=$('#fmDialog');
  if(fmDialog&&fmDialog.style.display==='flex'){FileManager.close();return true}
  const confirmDialog=$('#confirmDialog');
  if(confirmDialog&&confirmDialog.style.display==='flex'){
    const noBtn=$('#confirmNo');
    if(noBtn)noBtn.click();else confirmDialog.style.display='none';
    return true;
  }
  const reviewPop=$('#reviewPopover');
  if(reviewPop&&reviewPop.style.display==='block'){closeReviewPopover();return true}
  const notePanels=[...document.querySelectorAll('.reviewNotePanel')];
  if(notePanels.length){notePanels[notePanels.length-1].remove();return true}
  return false;
}
document.addEventListener('keydown',e=>{if(e.key==='Escape'){if(closeTopOverlayOnEscape())e.preventDefault()}});
document.querySelectorAll('.zoomControls').forEach(ctrl=>{
  const side=ctrl.dataset.side;
  ctrl.addEventListener('click',e=>{
    const btn=e.target.closest('button[data-zoom-act]');
    if(!btn||!side)return;
    const act=btn.dataset.zoomAct;
    if(act==='fit'){setPaneZoom(side,computeFitZoomForSide(side,DATA),true);return}
    if(act==='in'){setPaneZoom(side,zoomFor(side)+.05,true);return}
    if(act==='out'){setPaneZoom(side,zoomFor(side)-.05,true)}
  });
});
document.querySelectorAll('.zoomInput').forEach(inp=>{
  inp.addEventListener('keydown',e=>{
    if(e.key!=='Enter')return;
    const side=inp.id.startsWith('old')?'old':'new';
    const pct=Number(inp.value||100);
    if(!Number.isFinite(pct))return;
    setPaneZoom(side,pct/100,true);
  });
});
$('#viewer').addEventListener('wheel',e=>{
  if(!e.ctrlKey)return;
  const pane=e.target.closest('.pane');
  if(!pane)return;
  e.preventDefault();
  const side=pane.id==='oldPane'?'old':'new';
  const factor=e.deltaY<0?1.06:1/1.06;
  setPaneZoom(side,zoomFor(side)*factor,true);
},{passive:false});
function activateTab(name){document.querySelectorAll('.tabBtn').forEach(b=>b.classList.toggle('active',b.dataset.tab===name));document.querySelectorAll('.tabBody').forEach(b=>b.classList.toggle('active',b.id===`tab-${name}`));if(name==='snapshot')loadSnapshots()}
function apiBase(){return SNAPSHOT_ID?`/api/documents/${DOC_ID}/snapshots/${SNAPSHOT_ID}`:`/api/documents/${DOC_ID}/runs/${RUN_ID}`}
function isDiff(){return DATA?.mode==='diff'}
function clampZoom(v){return Math.max(.3,Math.min(3,Number(v)||1))}
function zoomFor(side){return clampZoom(ZOOM[side]??1)}
function updateZoomUi(side){
  const z=zoomFor(side);
  const pct=Math.round(z*100);
  const inp=$(`#${side}ZoomInput`);
  if(inp)inp.value=String(pct);
}
function setPaneZoom(side,next,redraw=true){
  ZOOM[side]=clampZoom(next);
  ZOOM_CUSTOM[side]=true;
  updateZoomUi(side);
  if(redraw&&DATA)draw(DATA);
}
function resetZoomState(){ZOOM_CUSTOM={old:false,new:false}}
function selectedInputName(inputId){const f=$(inputId)?.files?.[0];return f?f.name:''}
function updateUploadFileNames(){
  const baseEl=$('#baseSelectedName');
  const updateEl=$('#updateSelectedName');
  if(baseEl)baseEl.textContent=BASE_UPLOAD_NAME||'';
  if(updateEl)updateEl.textContent=UPDATE_UPLOAD_NAME||'';
}
function updatePaneTitles(){
  const defaultOld='Previous PDF';
  const defaultNew='Current PDF';
  const oldTitle=PREV_REPORT_FILENAME||DATA?.old_filename||defaultOld;
  const newTitle=CUR_REPORT_FILENAME||DATA?.new_filename||DATA?.filename||defaultNew;
  const oldEl=$('#oldTitle'),newEl=$('#newTitle');
  if(oldEl)oldEl.textContent=oldTitle;
  if(newEl)newEl.textContent=newTitle;
}
function filenameForSide(side){
  if(side==='old')return PREV_REPORT_FILENAME||DATA?.old_filename||'Previous PDF';
  return CUR_REPORT_FILENAME||DATA?.new_filename||DATA?.filename||'Current PDF';
}
function applyReportFilenamesFromMeta(meta,runId,viewer){
  const runs=Array.isArray(meta?.runs)?meta.runs:[];
  const runMeta=runs.find(r=>r.run_id===runId)||{};
  const prevMeta=runMeta.previous_run_id?runs.find(r=>r.run_id===runMeta.previous_run_id):null;
  CUR_REPORT_FILENAME=publicReportFilename(runMeta.filename,publicReportFilename(viewer?.new_filename||viewer?.filename,CUR_REPORT_FILENAME||'report.pdf'));
  PREV_REPORT_FILENAME=publicReportFilename(prevMeta?.filename,publicReportFilename(viewer?.old_filename,PREV_REPORT_FILENAME||'previous.pdf'));
}
function workspaceHasComparison(meta){return (meta?.runs||[]).some(r=>r.has_diff)}
function syncUploadFormsFromMeta(meta){
  const createForm=$('#createForm'),updateForm=$('#updateForm');
  if(!createForm||!updateForm)return;
  if(workspaceHasComparison(meta)){
    createForm.style.display='none';
    updateForm.style.display='flex';
    return;
  }
  const runs=meta?.runs||[];
  const hasReadyInitial=runs.some(r=>r.kind==='initial'&&r.status==='ready');
  createForm.style.display=hasReadyInitial?'none':'flex';
  updateForm.style.display=hasReadyInitial?'flex':'none';
}
async function loadWorkspaceRun(docId,meta,runId){
  if(!runId)return null;
  const viewer=await fetch(`/api/documents/${docId}/runs/${runId}`).then(r=>{if(!r.ok)throw new Error('run not found');return r.json()});
  RUN_ID=runId;LIVE_RUN_ID=runId;SNAPSHOT_ID=null;READ_ONLY=false;DATA=viewer;
  applyReportFilenamesFromMeta(meta,runId,viewer);
  syncUploadFormsFromMeta(meta);
  draw(viewer);
  syncUrlFromState();
  return viewer;
}
function updateWorkspaceNavButtons(){
  const a=$('#workspaceAnalysisBtn');
  if(a)a.classList.toggle('active',!document.body.classList.contains('homeMode'));
  updateFilesBtnLabel(DOC_ID);
}
function setAiRunState(next){
  AI_RUN_STATE={...AI_RUN_STATE,...next};
  if(AI_RUN_STATUS_TIMER){
    clearTimeout(AI_RUN_STATUS_TIMER);
    AI_RUN_STATUS_TIMER=null;
  }
  const box=$('#aiRunStatus'),text=$('#aiRunText');
  if(!box||!text)return;
  const msg=String(AI_RUN_STATE.message||'').trim();
  const show=AI_RUN_STATE.running||!!msg;
  box.classList.toggle('show',show);
  box.classList.toggle('done',!AI_RUN_STATE.running);
  text.textContent=msg;
  if(!AI_RUN_STATE.running&&msg){
    AI_RUN_STATUS_TIMER=setTimeout(()=>setAiRunState({message:''}),2600);
  }
}
function setChangeAiRunState(next){
  CHANGE_AI_RUN_STATE={...CHANGE_AI_RUN_STATE,...next};
  if(CHANGE_AI_RUN_STATUS_TIMER){
    clearTimeout(CHANGE_AI_RUN_STATUS_TIMER);
    CHANGE_AI_RUN_STATUS_TIMER=null;
  }
  const box=$('#changeAiRunStatus'),text=$('#changeAiRunText');
  if(!box||!text)return;
  const msg=String(CHANGE_AI_RUN_STATE.message||'').trim();
  const show=CHANGE_AI_RUN_STATE.running||!!msg;
  box.classList.toggle('show',show);
  box.classList.toggle('done',!CHANGE_AI_RUN_STATE.running);
  text.textContent=msg;
  if(!CHANGE_AI_RUN_STATE.running&&msg){
    CHANGE_AI_RUN_STATUS_TIMER=setTimeout(()=>setChangeAiRunState({message:''}),2600);
  }
}
function setReviewAiRunningIds(ids){REVIEW_AI_RUNNING_IDS=new Set(ids||[]);renderReviewList(REVIEWS)}
function setChangeAiRunningIds(ids){CHANGE_AI_RUNNING_IDS=new Set((ids||[]).map(Number));if(DATA)drawChanges(DATA)}
function updateAiButtons(){
  const reviewBtn=$('#runAiBtn');
  if(reviewBtn)reviewBtn.textContent=REVIEW_AI_ABORT_CONTROLLER?'Stop':'Run AI on All';
  const changeBtn=$('#runChangeAiBtn');
  if(changeBtn)changeBtn.textContent=CHANGE_AI_ABORT_CONTROLLER?'Stop':'Run AI on All';
}
function mergeChangeAssessmentItems(items){
  const incoming=[...(items||[])];
  const byId=new Map((CHANGE_AI_ASSESSMENT.items||[]).map(it=>[Number(it.change_id),it]));
  incoming.forEach(it=>{byId.set(Number(it.change_id),it)});
  CHANGE_AI_ASSESSMENT={...CHANGE_AI_ASSESSMENT,items:[...byId.values()]};
}
window.setChangeFilter=function(key,value){CHANGE_FILTERS[key]=value;if(DATA)drawChanges(DATA)}
window.resetChangeFilters=function(){CHANGE_FILTERS={ai:'all',risk:'all'};const vals={changeFilterAi:'all',changeFilterRisk:'all'};Object.keys(vals).forEach(id=>{const el=document.getElementById(id);if(el)el.value=vals[id]});if(DATA)drawChanges(DATA)}
async function renderHomeCards(){
  const grid=$('#homeGrid');
  if(!grid)return;
  grid.innerHTML='<div class="empty">Loading workspaces...</div>';
  try{
    const res=await fetch('/api/file-manager');
    const data=await res.json();
    const items=data.items||[];
    if(!items.length){
      grid.innerHTML='<div class="empty">No saved workspaces yet.</div>';
      return;
    }
    grid.innerHTML=items.map(item=>{
      const title=escapeHtml(item.title||item.name||'Workspace');
      const date=escapeHtml(formatDate(item.updated_at||item.created_at));
      const files=Number(item.file_count??item.run_count??0);
      const ws=escapeHtmlAttr(item.workspace_id||'');
      const active=DOC_ID&&DOC_ID===item.workspace_id?' style="border-color:#2563eb;box-shadow:0 0 0 2px rgba(37,99,235,.12)"':'';
      return `<button type="button" class="homeCard" data-home-open="${ws}"${active}><div class="homeCardTitle">${title}</div><div class="homeCardMeta">Updated: ${date}<br>Files: ${files}</div></button>`;
    }).join('');
  }catch(e){
    grid.innerHTML='<div class="empty">Failed to load workspaces.</div>';
  }
}
window.openHomeView=function(){
  HOME_RETURN_COLLAPSED=document.body.classList.contains('sidebarCollapsed');
  document.body.classList.add('homeMode');
  updateWorkspaceNavButtons();
  renderHomeCards();
}
window.closeHomeView=function(){
  document.body.classList.remove('homeMode');
  setSidebarCollapsed(HOME_RETURN_COLLAPSED);
  updateWorkspaceNavButtons();
}
window.openWorkspaceFromHome=async function(workspaceId){
  const id=workspaceId||DOC_ID;
  if(!id){toast('Select a workspace');return}
  closeHomeView();
  await openWorkspaceFromManager(id);
}
$('#homeGrid').addEventListener('click',e=>{
  const card=e.target.closest('[data-home-open]');
  if(!card)return;
  openWorkspaceFromHome(card.dataset.homeOpen);
});
function setBusy(busy,msg){$('#createBtn').disabled=busy;$('#updateBtn').disabled=busy;$('#status').textContent=msg||'Idle'}
function closeFileManager(){FileManager.close()}

function openSourcePicker(context,anchor){
  UPLOAD_CONTEXT=context;
  const picker=$('#sourcePicker');
  if(!picker||!anchor)return;
  const r=anchor.getBoundingClientRect();
  picker.style.display='flex';
  picker.style.left=`${Math.max(8,Math.min(window.innerWidth-170,r.left))}px`;
  picker.style.top=`${Math.min(window.innerHeight-120,r.bottom+6)}px`;
}
function closeSourcePicker(){const picker=$('#sourcePicker');if(picker)picker.style.display='none'}

async function fetchManagerReportFile(item){
  const workspaceId=item?.workspace_id||DOC_ID;
  const fileId=item?.file_id||item?.run_id||item?.latest_run_id;
  const downloadName=item?.filename||item?.latest_filename||item?.title||'report.pdf';
  if(!workspaceId||!fileId)throw new Error('No available file');
  const fileRes=await fetch(`/api/documents/${workspaceId}/files/${fileId}/report`);
  if(!fileRes.ok){
    let msg='Failed to fetch file from manager';
    try{const data=await fileRes.json();if(data.error)msg=data.error}catch(e){}
    throw new Error(msg);
  }
  const blob=await fileRes.blob();
  const safeName=String(downloadName).trim().toLowerCase().endsWith('.pdf')?String(downloadName).trim():`${String(downloadName).trim()||'report'}.pdf`;
  return new File([blob], safeName, {type:'application/pdf'});
}

async function runCreateWithBlob(blob,fileName){
  const file = new File([blob], fileName || 'report.pdf', {type:'application/pdf'});
  if(!file)return;
  const sourceWorkspaceId=PENDING_BASE_SOURCE_WORKSPACE_ID;
  REVIEWS=[];
  AI_ASSESSMENT={items:[]};
  clearReviewMarkers();
  renderReviewList(REVIEWS);
  BASE_UPLOAD_NAME=file.name||BASE_UPLOAD_NAME;
  updateUploadFileNames();
  setBusy(true,'Processing base report');
  $('#indexList').innerHTML='<div class="empty">Running OpenDataLoader and building the outline...</div>';
  $('#changes').innerHTML='<div class="empty">The base report has no comparison yet.</div>';
  const fd=new FormData(); fd.append('pdf',file,file.name); fd.append('title',file.name.replace(/\.pdf$/i,''));
  resetZoomState();
  try{
    const uploadUrl=DOC_ID?`/api/documents/${DOC_ID}/runs/initial`:'/api/documents';
    const res=await fetch(uploadUrl,{method:'POST',body:fd});
    const data=await res.json();
    if(!res.ok){
      if(res.status===409&&data.error==='workspace already has a comparison; use the update upload instead'&&DOC_ID){
        const meta=await fetch(`/api/documents/${DOC_ID}`).then(r=>r.json());
        resetRunSession(meta);
        throw new Error('This workspace already has a comparison. Use Updated Report upload.');
      }
      throw new Error(data.error==='workspace already has a comparison; use the update upload instead'?'This workspace already has a comparison. Use the update upload instead.':(data.error||res.status));
    }
    DOC_ID=data.workspace_id; RUN_ID=data.run_id; SNAPSHOT_ID=null; READ_ONLY=false; DATA=data.result;
    if(sourceWorkspaceId){
      await fetch(`/api/documents/${DOC_ID}/runs/${RUN_ID}/import-reviews`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source_workspace_id:sourceWorkspaceId})});
    }
    CUR_REPORT_FILENAME=file.name||CUR_REPORT_FILENAME;
    PREV_REPORT_FILENAME='';
    // Keep workspace name independent from uploaded/analyzed report filename.
    updateWorkspaceTitle();
    $('#updateForm').style.display='flex';
    setSidebarCollapsed(false);
    draw(DATA); loadSnapshots(); activateTab('review'); syncUrlFromState(); setBusy(false,'Base report ready');
  }catch(err){setBusy(false,'Error: '+err.message)}
}

async function compareWithManagerFile(item){
  if(!DOC_ID){toast('Open a workspace first.');return}
  try{
    const file=await fetchManagerReportFile(item);
    PENDING_UPDATE_FILE=file;
    UPDATE_UPLOAD_NAME=file.name||UPDATE_UPLOAD_NAME;
    updateUploadFileNames();
    closeFileManager();
    toast('Selected update file from manager');
  }catch(e){toast(e.message||'Failed to select update file')}
}

async function useManagerFileAsBase(item){
  try{
    const file=await fetchManagerReportFile(item);
    PENDING_BASE_FILE=file;
    PENDING_BASE_SOURCE_WORKSPACE_ID=item.workspace_id||null;
    BASE_UPLOAD_NAME=file.name||BASE_UPLOAD_NAME;
    updateUploadFileNames();
    closeFileManager();
    toast('Selected base file from manager');
  }catch(e){toast(e.message||'Failed to select base file')}
}

async function openManagerFile(item){
  const workspaceId=item?.workspace_id||DOC_ID;
  const fileId=item?.file_id||item?.run_id||item?.latest_run_id;
  if(!workspaceId||!fileId){toast('No file selected');return}
  FileManager.close();
  setBusy(true,'Opening saved file');
  try{
    if(workspaceId!==DOC_ID){
      DOC_ID=workspaceId;
      const wsMeta=await fetch(`/api/documents/${workspaceId}`).then(r=>r.json());
      WORKSPACE_TITLE=wsMeta.title||'Workspace';
      updateWorkspaceTitle();
    }
    const res=await fetch(`/api/documents/${workspaceId}/files/${fileId}/open`,{method:'POST'});
    const data=await res.json();
    if(!res.ok)throw new Error(data.error||res.status);
    const meta=await fetch(`/api/documents/${workspaceId}`).then(r=>r.json());
    RUN_ID=data.run_id;LIVE_RUN_ID=data.run_id;SNAPSHOT_ID=null;READ_ONLY=false;DATA=data.result;
    PENDING_BASE_FILE=null;PENDING_UPDATE_FILE=null;
    applyReportFilenamesFromMeta(meta,data.run_id,data.result);
    syncUploadFormsFromMeta(meta);
    resetZoomState();
    setSidebarCollapsed(false);
    draw(DATA);
    await loadSnapshots();
    syncUrlFromState();
    activateTab('review');
    setBusy(false,'Ready');
    toast('Opened with saved reviews');
  }catch(e){setBusy(false,'Error: '+(e.message||'Failed to open file'))}
}

async function openWorkspaceFromManager(docId){
  if(!docId)return;
  FileManager.close();
  try{
    const meta=await fetch(`/api/documents/${docId}`).then(r=>{if(!r.ok)throw new Error('workspace not found');return r.json()});
    await openWorkspaceSession(docId);
    toast('Workspace opened');
  }catch(e){toast('Failed to open workspace')}
}

FileManager.init({
  getCtx:()=>({docId:DOC_ID}),
  onOpenFile:openManagerFile,
  onSelectFile:async item=>{
    const baseAllowed=$('#createForm')&&$('#createForm').style.display!=='none';
    if(UPLOAD_CONTEXT==='base'&&baseAllowed)await useManagerFileAsBase(item);
    else await compareWithManagerFile(item);
  },
  onOpenWorkspace:openWorkspaceFromManager,
  onRenamed:async (docId,title)=>{if(DOC_ID===docId){WORKSPACE_TITLE=title;updateWorkspaceTitle()}},
  onDeleted:async docId=>{
    if(DOC_ID!==docId)return;
    releaseAnalysisSession({keepWorkspace:false});
    try{
      const ws=await ensureDefaultWorkspace();
      DOC_ID=ws.workspace_id;
      WORKSPACE_TITLE=ws.title||'Workspace';
      updateWorkspaceTitle();
      syncUrlFromState();
      await loadSnapshots();
    }catch(e){}
  },
});
$('#createForm').addEventListener('submit',async e=>{
  e.preventDefault();
  const selectedLocal=$('#initialFile').files[0];
  const file=selectedLocal||PENDING_BASE_FILE; if(!file){toast('Select a base file first.');return}
  if(selectedLocal)PENDING_BASE_SOURCE_WORKSPACE_ID=null;
  await runCreateWithBlob(file,file.name);
  PENDING_BASE_FILE=null;
  PENDING_BASE_SOURCE_WORKSPACE_ID=null;
  BASE_UPLOAD_NAME='';
  const baseInput=$('#initialFile');
  if(baseInput)baseInput.value='';
  updateUploadFileNames();
});

async function runCompareWithBlob(blob,fileName){
  if(!DOC_ID){toast('Upload a base report first.');return}
  const file = new File([blob], fileName || 'report.pdf', {type:'application/pdf'});
  UPDATE_UPLOAD_NAME=file.name||UPDATE_UPLOAD_NAME;
  updateUploadFileNames();
  setBusy(true,'Uploading updated report');
  const fd=new FormData(); fd.append('pdf',file,file.name);
  resetZoomState();
  try{
    let res=await fetch(`/api/documents/${DOC_ID}/runs`,{method:'POST',body:fd});
    let data=await res.json();
    if(!res.ok)throw new Error(data.error||res.status);
    RUN_ID=data.run_id; SNAPSHOT_ID=null; READ_ONLY=false;
    PREV_REPORT_FILENAME=CUR_REPORT_FILENAME||PREV_REPORT_FILENAME;
    CUR_REPORT_FILENAME=file.name||CUR_REPORT_FILENAME;
    $('#status').textContent='Comparing reports';
    res=await fetch(`${apiBase()}/diff`,{method:'POST'});
    data=await res.json();
    if(!res.ok)throw new Error(data.error||res.status);
    DATA=data.result;
    draw(DATA); loadSnapshots(); activateTab('diff'); syncUrlFromState(); setBusy(false,'Comparison complete');
  }catch(err){setBusy(false,'Error: '+err.message)}
}
$('#updateForm').addEventListener('submit',async e=>{
  e.preventDefault();
  const file=$('#updateFile').files[0]||PENDING_UPDATE_FILE; if(!file){toast('Select an updated file first.');return}
  if($('#updateFile').files[0])PENDING_UPDATE_FILE=null;
  await runCompareWithBlob(file,file.name);
  PENDING_UPDATE_FILE=null;
  UPDATE_UPLOAD_NAME='';
  const updateInput=$('#updateFile');
  if(updateInput)updateInput.value='';
  updateUploadFileNames();
});

function draw(d){
  $('#viewer').classList.toggle('single',!isDiff());
  if(!isDiff())REVIEW_SIDE='new';
  updateWorkspaceTitle();
  if(READ_ONLY)$('#status').innerHTML='<span class="readonlyBadge">Snapshot</span>';
  updatePaneTitles();
  const changes=d.changes||[];
  const addGroups=changes.filter(c=>String(c.new_text||'').trim()).length;
  const delGroups=changes.filter(c=>String(c.old_text||'').trim()).length;
  $('#diffStats').innerHTML=`<span class="addStat">+ ${addGroups}</span> / <span class="delStat">-${delGroups}</span>`;
  if(!ZOOM_CUSTOM.old)ZOOM.old=computeFitZoomForSide('old',d);
  if(!ZOOM_CUSTOM.new)ZOOM.new=computeFitZoomForSide('new',d);
  updateZoomUi('old');
  updateZoomUi('new');
  drawIndex(d); drawChanges(d);
  if(isDiff())drawPages('old',d.old_page_count||0,d.old_page_sizes||[],d.highlights_old||[]);
  else $('#oldPages').innerHTML='';
  drawPages('new',d.new_page_count||d.page_count||0,d.new_page_sizes||d.page_sizes||[],d.highlights_new||[]);
  $('#reviewSubtabs').style.display=isDiff()?'flex':'none';
  updateReviewSubtabs();
  setupSemantic(d);
  loadChangeAssessment(runSessionKey());
}
function computeFitZoomForSide(side,d){
  if(!d)return 1;
  const pad=8;
  if(side==='old'){
    const sizes=(d.old_page_sizes||[]);
    if(!isDiff()||!sizes.length)return zoomFor('old');
    const w=Math.max(...sizes.map(s=>s.width||1));
    return clampZoom(($('#oldPane').clientWidth-pad)/w);
  }
  const sizes=d.new_page_sizes||d.page_sizes||[];
  if(!sizes.length)return zoomFor('new');
  const w=Math.max(...sizes.map(s=>s.width||1));
  return clampZoom(($('#newPane').clientWidth-pad)/w);
}
function drawIndex(d){const items=d.index_items||[];if(!items.length){$('#indexList').innerHTML='<div class="empty">Unable to build an outline.</div>';return}$('#indexList').innerHTML=items.map((it,i)=>`<div class="indexItem" data-idx="${i}" onclick="goIndex(${i})"><div class="type">${it.type==='fs'?'FINANCIAL STATEMENT':it.type==='note'?'NOTE':String(it.type||'SECTION').toUpperCase()} 쨌 p.${it.page||'-'}</div><div>${escapeHtml(trim(it.label||it.title,180))}</div></div>`).join('')}
function drawChanges(d){
  const changes=d.changes||[];
  const bar=$('#changeAiBar');
  if(bar)bar.style.display=isDiff()?'flex':'none';
  const fbar=$('#changeFilterBar');
  if(fbar)fbar.style.display=isDiff()?'flex':'none';
  if(!isDiff()){
    $('#changes').innerHTML='<div class="empty">The base report has no comparison yet. Upload an updated report to compare versions.</div>';
    return;
  }
  const filtered=changes.filter(c=>{
    const ai=changeAiItemForId(c.id);
    const risk=ai?normalizeChangeVerdict(ai.verdict):null;
    if(CHANGE_FILTERS.ai==='checked'&&!ai)return false;
    if(CHANGE_FILTERS.ai==='unchecked'&&ai)return false;
    if(CHANGE_FILTERS.risk!=='all'&&risk!==CHANGE_FILTERS.risk)return false;
    return true;
  });
  if(!filtered.length){
    $('#changes').innerHTML='<div class="empty">No changes found.</div>';
    return;
  }
  $('#changes').innerHTML=filtered.map(c=>{
    const ai=changeAiItemForId(c.id);
    const actions=READ_ONLY?'':`<div class="changeActions"><button onclick="event.stopPropagation();runSingleChangeAi(${Number(c.id)})">AI</button><button onclick="event.stopPropagation();forwardChangeAi(${Number(c.id)})">Forward</button></div>`;
    return `<div class="change" data-id="${c.id}" onclick="goChange(${c.id})"><div class="meta">#${c.id} previous p.${c.old_page||'-'} / current p.${c.new_page||'-'} 쨌 removed ${c.old_highlight_ids?.length||0} 쨌 added ${c.new_highlight_ids?.length||0}</div>${c.old_text?`<div><span class="delText">- ${escapeHtml(trim(c.old_text))}</span></div>`:''}${c.new_text?`<div><span class="addText">+ ${escapeHtml(trim(c.new_text))}</span></div>`:''}${renderChangeAiReasonForList(c.id,ai)}${actions}</div>`;
  }).join('');
}
function drawPages(side,count,sizes,highlights){const z=zoomFor(side);const root=side==='old'?$('#oldPages'):$('#newPages');root.querySelectorAll('img').forEach(img=>{img.removeAttribute('src');img.src=''});root.innerHTML='';const byPage=new Map();for(const h of highlights||[]){if(!byPage.has(h.page))byPage.set(h.page,[]);byPage.get(h.page).push(h)}for(let p=1;p<=count;p++){const size=sizes[p-1];if(!size)continue;const page=document.createElement('div');page.className='page';page.id=`${side}-page-${p}`;page.style.width=`${size.width*z}px`;page.style.height=`${size.height*z}px`;const img=document.createElement('img');img.loading='lazy';img.draggable=false;img.src=`${apiBase()}/page/${side}/${p}?zoom=${z}`;page.appendChild(img);for(const h of byPage.get(p)||[]){const[x0,y0,x1,y1]=h.bbox;const el=document.createElement('div');el.className=`hl ${h.type}`;el.id=h.id;el.dataset.changeId=h.change_id;el.dataset.changeIds=(h.change_ids||[h.change_id]).join(',');el.style.left=`${x0*z}px`;el.style.top=`${y0*z}px`;el.style.width=`${Math.max(1,(x1-x0)*z)}px`;el.style.height=`${Math.max(2,(y1-y0)*z)}px`;page.appendChild(el)}root.appendChild(page)}}

window.goIndex=function(i){const it=DATA?.index_items?.[i];if(!it)return;document.querySelectorAll('.indexItem').forEach(e=>e.classList.toggle('active',Number(e.dataset.idx)===i));scrollBboxIntoPane($('#newPane'),'new',it.page,it.bbox||[0,0,1,1]);if(isDiff()&&it.old_page!=null)scrollBboxIntoPane($('#oldPane'),'old',it.old_page,it.old_bbox||[0,0,1,1])}
window.goChange=function(id){if(!isDiff())return;const selectedId=Number(id);document.querySelectorAll('.change').forEach(e=>e.classList.toggle('active',Number(e.dataset.id)===selectedId));document.querySelectorAll('.hl').forEach(e=>{const ids=String(e.dataset.changeIds||e.dataset.changeId||'').split(',').map(Number).filter(x=>!Number.isNaN(x));e.classList.toggle('focus',ids.includes(selectedId))});scrollToChange('oldPane',selectedId);scrollToChange('newPane',selectedId)}
function scrollToChange(paneId,changeId){const pane=document.getElementById(paneId);if(!pane||!DATA)return;const target=[...pane.querySelectorAll('.hl')].find(el=>{const ids=String(el.dataset.changeIds||el.dataset.changeId||'').split(',').map(Number).filter(x=>!Number.isNaN(x));return ids.includes(Number(changeId))});if(target){scrollElementIntoPane(pane,target);return}const change=(DATA.changes||[]).find(c=>Number(c.id)===Number(changeId));if(!change)return;const side=paneId.toLowerCase().includes('old')?'old':'new';const anchor=side==='old'?change.old_anchor:change.new_anchor;if(anchor&&anchor.page&&anchor.bbox)scrollBboxIntoPane(pane,side,anchor.page,anchor.bbox)}
function scrollElementIntoPane(pane,el){const pr=pane.getBoundingClientRect();const er=el.getBoundingClientRect();pane.scrollTop+=er.top-pr.top-pane.clientHeight*.35;pane.scrollLeft+=er.left-pr.left-pane.clientWidth*.35}
function scrollBboxIntoPane(pane,side,pageNo,bbox){const z=zoomFor(side);if(!pane||!pageNo)return;const page=document.getElementById(`${side}-page-${pageNo}`);if(!page)return;const[x0,y0]=bbox;const pageRect=page.getBoundingClientRect();const paneRect=pane.getBoundingClientRect();pane.scrollTop+=pageRect.top+y0*z-paneRect.top-pane.clientHeight*.35;pane.scrollLeft+=pageRect.left+x0*z-paneRect.left-pane.clientWidth*.35}

function bboxOverlap(a,b){return !(a[2]<b[0]||b[2]<a[0]||a[3]<b[1]||b[3]<a[1])}
async function setupSemantic(d){
  const sessionKey=runSessionKey();
  EQUAL=buildEqual(d.semantic_map);
  WORDS={old:[],new:[]};
  CHARS={old:[],new:[]};
  try{
    const jobs=[
      fetch(`${apiBase()}/words/new`).then(r=>r.ok?r.json():[]),
      fetch(`${apiBase()}/chars/new`).then(r=>r.ok?r.json():[])
    ];
    if(isDiff()){
      jobs.unshift(fetch(`${apiBase()}/chars/old`).then(r=>r.ok?r.json():[]));
      jobs.unshift(fetch(`${apiBase()}/words/old`).then(r=>r.ok?r.json():[]));
    }
    const result=await Promise.all(jobs);
    if(sessionKey!==runSessionKey())return;
    if(isDiff()){WORDS.old=result[0]||[];CHARS.old=result[1]||[];WORDS.new=result[2]||[];CHARS.new=result[3]||[]}
    else{WORDS.new=result[0]||[];CHARS.new=result[1]||[]}
  }catch(e){if(sessionKey===runSessionKey()){WORDS={old:[],new:[]};CHARS={old:[],new:[]}}}
  if(sessionKey===runSessionKey())await loadReviews(sessionKey);
}
function buildEqual(sm){const pairs=(sm&&sm.equal_words)||[];const e={pairs,newToOld:new Map(),oldToNew:new Map(),newEqual:new Set(),oldEqual:new Set()};pairs.forEach(p=>{e.newToOld.set(p.new_word_id,p);e.oldToNew.set(p.old_word_id,p);e.newEqual.add(p.new_word_id);e.oldEqual.add(p.old_word_id)});return e}

['old','new'].forEach(setupSelection);
function pageWordsInReadOrder(side,pageNo){
  return (WORDS[side]||[])
    .filter(w=>w.page===pageNo)
    .slice()
    .sort((a,b)=>(a.block-b.block)||(a.line-b.line)||(a.word_no-b.word_no)||(a.bbox[1]-b.bbox[1])||(a.bbox[0]-b.bbox[0]));
}
function pageCharsInReadOrder(side,pageNo){
  return (CHARS[side]||[])
    .filter(ch=>ch.page===pageNo)
    .slice()
    .sort((a,b)=>((a.block??0)-(b.block??0))||((a.line??0)-(b.line??0))||((a.word_no??0)-(b.word_no??0))||((a.char_index??0)-(b.char_index??0))||(a.order-b.order)||(a.bbox[1]-b.bbox[1])||(a.bbox[0]-b.bbox[0]));
}
async function ensureSelectionAssets(side){
  if((WORDS[side]||[]).length&&(CHARS[side]||[]).length)return true;
  try{
    const [words,chars]=await Promise.all([
      fetch(`${apiBase()}/words/${side}`).then(r=>r.ok?r.json():[]),
      fetch(`${apiBase()}/chars/${side}`).then(r=>r.ok?r.json():[])
    ]);
    WORDS[side]=words||[];
    CHARS[side]=chars||[];
  }catch(e){
    WORDS[side]=WORDS[side]||[];
    CHARS[side]=CHARS[side]||[];
  }
  return (WORDS[side]||[]).length>0;
}
function nearestCharAt(side,pageNo,x,y){
  const chars=pageCharsInReadOrder(side,pageNo);
  let best=null,bestScore=Infinity;
  for(const ch of chars){
    const [x0,y0,x1,y1]=ch.bbox;
    const inside=x>=x0-2&&x<=x1+2&&y>=y0-4&&y<=y1+4;
    const cx=Math.max(x0,Math.min(x,x1)), cy=Math.max(y0,Math.min(y,y1));
    const dist=(x-cx)*(x-cx)+(y-cy)*(y-cy);
    const score=inside?dist:dist+900;
    if(score<bestScore){best=ch;bestScore=score}
  }
  return best;
}
function charsFromSweep(side,pageNo,startPt,endPt,rectDoc){
  const chars=pageCharsInReadOrder(side,pageNo);
  if(!chars.length)return [];
  const start=nearestCharAt(side,pageNo,startPt.x,startPt.y);
  const end=nearestCharAt(side,pageNo,endPt.x,endPt.y);
  if(start&&end){
    const a=chars.findIndex(ch=>ch.idx===start.idx), b=chars.findIndex(ch=>ch.idx===end.idx);
    if(a>=0&&b>=0){
      const lo=Math.min(a,b), hi=Math.max(a,b);
      return chars.slice(lo,hi+1);
    }
  }
  return chars.filter(ch=>bboxOverlap(ch.bbox,rectDoc));
}
function wordIdsFromChars(chars){
  const ids=[];
  const seen=new Set();
  chars.forEach(ch=>{if(ch.word_id!=null&&!seen.has(ch.word_id)){seen.add(ch.word_id);ids.push(ch.word_id)}});
  return ids;
}
function nearestWordAt(side,pageNo,x,y){
  const words=pageWordsInReadOrder(side,pageNo);
  let best=null,bestScore=Infinity;
  for(const w of words){
    const [x0,y0,x1,y1]=w.bbox;
    const inside=x>=x0-3&&x<=x1+3&&y>=y0-4&&y<=y1+4;
    const cx=Math.max(x0,Math.min(x,x1)), cy=Math.max(y0,Math.min(y,y1));
    const dist=(x-cx)*(x-cx)+(y-cy)*(y-cy);
    const score=inside?dist:dist+900;
    if(score<bestScore){best=w;bestScore=score}
  }
  return best;
}
function idsFromTextSweep(side,pageNo,startPt,endPt,rectDoc){
  const chars=charsFromSweep(side,pageNo,startPt,endPt,rectDoc);
  if(chars.length)return wordIdsFromChars(chars);
  const words=pageWordsInReadOrder(side,pageNo);
  if(!words.length)return [];
  const start=nearestWordAt(side,pageNo,startPt.x,startPt.y);
  const end=nearestWordAt(side,pageNo,endPt.x,endPt.y);
  if(start&&end){
    const a=words.findIndex(w=>w.idx===start.idx), b=words.findIndex(w=>w.idx===end.idx);
    if(a>=0&&b>=0){
      const lo=Math.min(a,b), hi=Math.max(a,b);
      return words.slice(lo,hi+1).map(w=>w.idx);
    }
  }
  return words.filter(w=>bboxOverlap(w.bbox,rectDoc)).map(w=>w.idx);
}
function showCharSweepPreview(side,pageNo,startPt,endPt,rectDoc){
  const z=zoomFor(side);
  clearPreview();
  const chars=charsFromSweep(side,pageNo,startPt,endPt,rectDoc);
  const pg=document.getElementById(`${side}-page-${pageNo}`);
  if(!pg)return;
  const groups=new Map();
  chars.forEach(ch=>{const key=`${ch.block??0}:${ch.line??Math.round(ch.bbox[1])}`;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(ch)});
  groups.forEach(lineChars=>{
    const xs=[],ys=[];
    lineChars.forEach(ch=>{const[x0,y0,x1,y1]=ch.bbox;xs.push(x0,x1);ys.push(y0,y1)});
    const x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys),y1=Math.max(...ys);
    const el=document.createElement('div');el.className='textsel';
    el.style.left=x0*z+'px';el.style.top=y0*z+'px';
    el.style.width=Math.max(1,(x1-x0)*z)+'px';el.style.height=Math.max(2,(y1-y0)*z)+'px';
    pg.appendChild(el);
  });
}
function setupSelection(side){
  const pages=side==='old'?$('#oldPages'):$('#newPages');
  pages.addEventListener('mousedown',async e=>{
    if(READ_ONLY)return;
    if(e.button!==0||e.target.closest('.review,.reviewNote,.reviewNotePanel'))return;
    if(side==='old'&&!isDiff())return;
    const pageEl=e.target.closest('.page');if(!pageEl)return;
    if(!(WORDS[side]||[]).length){
      const ok=await ensureSelectionAssets(side);
      if(!ok){toast('Text index is still loading. Try again in a moment.');return}
    }
    e.preventDefault();
    const r0=pageEl.getBoundingClientRect();
    const sx=e.clientX-r0.left, sy=e.clientY-r0.top;
    const z=zoomFor(side);
    const startDoc={x:sx/z,y:sy/z};
    const box=document.createElement('div');box.className='selbox';box.style.display='block';pageEl.appendChild(box);
    document.body.style.userSelect='none';
    let rect=[sx,sy,sx,sy];
    const move=ev=>{const rr=pageEl.getBoundingClientRect();let cx=Math.max(0,Math.min(ev.clientX-rr.left,pageEl.clientWidth)),cy=Math.max(0,Math.min(ev.clientY-rr.top,pageEl.clientHeight));rect=[Math.min(sx,cx),Math.min(sy,cy),Math.max(sx,cx),Math.max(sy,cy)];box.style.left=rect[0]+'px';box.style.top=rect[1]+'px';box.style.width=(rect[2]-rect[0])+'px';box.style.height=(rect[3]-rect[1])+'px';showCharSweepPreview(side,Number(pageEl.id.split('-').pop()),startDoc,{x:cx/z,y:cy/z},rect.map(v=>v/z))};
    const up=ev=>{document.removeEventListener('mousemove',move);document.removeEventListener('mouseup',up);document.body.style.userSelect='';box.remove();const rr=pageEl.getBoundingClientRect();const endDoc={x:Math.max(0,Math.min(ev.clientX-rr.left,pageEl.clientWidth))/z,y:Math.max(0,Math.min(ev.clientY-rr.top,pageEl.clientHeight))/z};finishSelection(side,pageEl,rect,{x:ev.clientX,y:ev.clientY},startDoc,endDoc)};
    document.addEventListener('mousemove',move);document.addEventListener('mouseup',up);
  });
}
function finishSelection(side,pageEl,rectPx,xy,startDoc,endDoc){
  const z=zoomFor(side);
  if((rectPx[2]-rectPx[0])<3&&(rectPx[3]-rectPx[1])<3)return;
  const sel=rectPx.map(v=>v/z);
  const pageNo=Number(pageEl.id.split('-').pop());
  const ids=idsFromTextSweep(side,pageNo,startDoc,endDoc,sel);
  if(!ids.length){toast('No text was selected.');return}
  openPopover(side,pageNo,sel,ids,xy);
}
function clearPreview(){document.querySelectorAll('.review.preview,.textsel').forEach(e=>e.remove())}
function closeReviewPopover(){const pop=$('#reviewPopover');if(!pop)return;pop.style.display='none';clearPreview();$('#pvSave').onclick=null;$('#pvCancel').onclick=null}
function openPopover(side,pageNo,sel,ids,xy){const pop=$('#reviewPopover');$('#pvComment').value='';pop.style.display='block';const w=pop.offsetWidth,h=pop.offsetHeight;pop.style.left=Math.max(8,Math.min(xy.x+8,window.innerWidth-w-8))+'px';pop.style.top=Math.max(8,Math.min(xy.y+8,window.innerHeight-h-8))+'px';$('#pvComment').focus();$('#pvCancel').onclick=closeReviewPopover;$('#pvSave').onclick=async()=>{const comment=$('#pvComment').value.trim();await submitReview(side,pageNo,sel,ids,comment);closeReviewPopover()}}
async function submitReview(side,pageNo,sel,ids,comment){if(READ_ONLY){toast('Snapshot is read-only');return}try{const res=await fetch(`${apiBase()}/reviews`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({side,page:pageNo,rect:sel,word_ids:ids,comment})});const rev=await res.json();if(!res.ok||rev.error){toast('Failed to save comment: '+(rev.error||res.status));return}REVIEW_SIDE=side==='old'?'old':'new';await loadReviews();activateTab('review');updateReviewSubtabs();focusReviewById(rev.review_id)}catch(e){toast('Comment save failed')}}

async function loadAssessment(){if(!DATA||!isDiff()){AI_ASSESSMENT={items:[]};return}try{AI_ASSESSMENT=await fetch(`${apiBase()}/assess`).then(r=>r.ok?r.json():{items:[]})}catch(e){AI_ASSESSMENT={items:[]}}}
async function loadChangeAssessment(sessionKey=runSessionKey()){
  if(!DATA||!isDiff()){CHANGE_AI_ASSESSMENT={items:[]};drawChanges(DATA||{});return}
  try{CHANGE_AI_ASSESSMENT=await fetch(`${apiBase()}/change-assess`).then(r=>r.ok?r.json():{items:[]})}
  catch(e){CHANGE_AI_ASSESSMENT={items:[]}}
  if(sessionKey!==runSessionKey())return;
  drawChanges(DATA);
}
async function loadReviews(sessionKey=runSessionKey()){if(!DATA)return;try{REVIEWS=await fetch(`${apiBase()}/reviews`).then(r=>r.ok?r.json():[])}catch(e){REVIEWS=[]}if(sessionKey!==runSessionKey())return;await loadAssessment();if(sessionKey!==runSessionKey())return;drawReviewMarkers(REVIEWS);renderReviewList(REVIEWS);if(REVIEW_DETAIL_MODAL_ID&&$('#reviewDetailModal')?.style.display==='flex')renderReviewDetailModal()}
async function loadSnapshots(){if(!DOC_ID)return;try{const data=await fetch(`/api/documents/${DOC_ID}/snapshots`).then(r=>r.ok?r.json():{snapshots:[]});renderSnapshots(data.snapshots||[])}catch(e){renderSnapshots([])}}
function renderSnapshots(items){const pane=$('#snapshotPane');if(!items.length){pane.innerHTML='<div class="empty">Save a snapshot to preserve the current review state.</div>';return}pane.innerHTML=items.map(s=>`<div class="snapshotRow"><div class="meta">${escapeHtml((s.created_at||'').slice(0,19).replace('T',' '))} 쨌 ${s.mode||'-'} 쨌 ${s.has_ai_assessment?'Review AI':'No Review AI'} 쨌 ${s.has_change_assessment?'Change AI':'No Change AI'}</div><div>${escapeHtml(s.label||s.filename||s.snapshot_id)}</div><div class="meta">${s.open_reviews||0} open / ${s.closed_reviews||0} closed</div><div class="rvActions"><button onclick="event.stopPropagation();openSnapshot('${s.snapshot_id}')">Open</button><button onclick="event.stopPropagation();deleteSnapshot('${s.snapshot_id}')">Delete</button></div></div>`).join('')}
window.deleteSnapshot=async function(id){if(!DOC_ID||!id)return;if(!await askConfirm('Delete this snapshot?'))return;try{const res=await fetch(`/api/documents/${DOC_ID}/snapshots/${id}`,{method:'DELETE'});const data=await res.json();if(!res.ok||data.error){toast(data.error||'Delete failed');return}if(SNAPSHOT_ID===id){SNAPSHOT_ID=null;READ_ONLY=false}await loadSnapshots();toast('Snapshot deleted')}catch(e){toast('Delete failed')}}
window.saveSnapshot=async function(){if(!DOC_ID||!RUN_ID||SNAPSHOT_ID){toast('Open a live run before saving a snapshot.');return}try{const label=$('#snapshotLabel')?.value||'';const res=await fetch(`/api/documents/${DOC_ID}/snapshots`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({label,run_id:RUN_ID})});const data=await res.json();if(!res.ok||data.error){toast(data.error||'Snapshot save failed');return}if($('#snapshotLabel'))$('#snapshotLabel').value='';await loadSnapshots();toast('Snapshot saved to workspace')}catch(e){toast('Snapshot save failed')}}
window.openSnapshot=async function(id){try{const res=await fetch(`/api/documents/${DOC_ID}/snapshots/${id}`);const data=await res.json();if(!res.ok||data.error){toast(data.error||'Snapshot open failed');return}SNAPSHOT_ID=id;RUN_ID=data.snapshot?.source_run_id||data.snapshot?.run_id||RUN_ID;READ_ONLY=true;DATA=data.result;draw(DATA);activateTab('review');syncUrlFromState();toast('Snapshot opened')}catch(e){toast('Snapshot open failed')}}
function clearReviewMarkers(){document.querySelectorAll('.review:not(.preview),.reviewNote,.reviewNotePanel,.reviewAnchorHover').forEach(e=>e.remove())}
function drawReviewMarkers(reviews){clearReviewMarkers();reviews.forEach(rv=>{if(isDiff())drawSideMarker('old',rv.old_word_ids,rv);drawSideMarker('new',rv.new_word_ids,rv)})}
function uniqueValidWordIds(side,ids){
  const seen=new Set(), out=[];
  (ids||[]).forEach(id=>{if(!seen.has(id)&&WORDS[side]?.[id]){seen.add(id);out.push(id)}});
  return out;
}
function reviewWordIdsForSide(rv,side){
  const direct=uniqueValidWordIds(side,rv[side+'_word_ids']||[]);
  if(direct.length)return direct;
  if(side==='old'&&rv.new_word_ids?.length)return uniqueValidWordIds('old',rv.new_word_ids.map(id=>EQUAL.newToOld.get(id)?.old_word_id).filter(id=>id!=null));
  if(side==='new'&&rv.old_word_ids?.length)return uniqueValidWordIds('new',rv.old_word_ids.map(id=>EQUAL.oldToNew.get(id)?.new_word_id).filter(id=>id!=null));
  return [];
}
function scrollWordIdsIntoPane(side,ids){
  const first=ids?.length?WORDS[side]?.[ids[0]]:null;
  if(first)scrollBboxIntoPane($(`#${side}Pane`),side,first.page,first.bbox);
  return !!first;
}
function scrollReviewChangeFallback(side,rv){
  if(!isDiff()||!(rv.change_ids||[]).length)return false;
  const paneId=side==='old'?'oldPane':'newPane', pane=document.getElementById(paneId);
  if(!pane)return false;
  const ids=new Set((rv.change_ids||[]).map(Number));
  const target=[...pane.querySelectorAll('.hl')].find(el=>String(el.dataset.changeIds||el.dataset.changeId||'').split(',').map(Number).some(id=>ids.has(id)));
  if(target){scrollElementIntoPane(pane,target);return true}
  const change=(DATA?.changes||[]).find(c=>ids.has(Number(c.id)));
  const anchor=change&&(side==='old'?change.old_anchor:change.new_anchor);
  if(anchor?.page&&anchor?.bbox){scrollBboxIntoPane(pane,side,anchor.page,anchor.bbox);return true}
  return false;
}
function showReviewHover(rv,displaySide){
  clearAnchorHover();
  const side=displaySide||'new';
  const ids=reviewWordIdsForSide(rv,side);
  if(ids.length)showAnchorFocus(side,ids,'reviewAnchorHover');
}
function markerPositionForBbox(pageEl,side,pageNo,bbox){
  const z=zoomFor(side);
  const pageW=pageEl.clientWidth/z,pageH=pageEl.clientHeight/z,iconW=24/z,iconH=22/z;
  const pageWords=(WORDS[side]||[]).filter(x=>x.page===pageNo);
  const textLeft=pageWords.length?Math.min(...pageWords.map(x=>x.bbox[0]||0)):bbox[0];
  const textRight=pageWords.length?Math.max(...pageWords.map(x=>x.bbox[2]||0)):bbox[2];
  const rightX=Math.min(pageW-iconW-2,Math.max(textRight+8,bbox[2]+8));
  const leftX=Math.max(2,Math.min(textLeft-iconW-8,bbox[0]-iconW-8));
  const lanes=[];
  if(rightX>=2&&rightX+iconW<=pageW-2)lanes.push(rightX);
  if(leftX>=2&&leftX+iconW<=pageW-2)lanes.push(leftX);
  if(!lanes.length)lanes.push(Math.max(2,Math.min(pageW-iconW-2,bbox[2]+8)));
  const used=Array.from(pageEl.querySelectorAll('.reviewNote')).map(n=>({x:(parseFloat(n.style.left)||0)/z,y:(parseFloat(n.style.top)||0)/z,w:iconW,h:iconH}));
  const baseY=Math.max(2,Math.min(pageH-iconH-2,bbox[1]-2));
  const step=iconH+4/z,offsets=[0,step,-step,step*2,-step*2,step*3,-step*3,step*4,-step*4,step*5,-step*5];
  const overlaps=(a,b)=>!(a.x+a.w<b.x||b.x+b.w<a.x||a.y+a.h<b.y||b.y+b.h<a.y);
  for(const off of offsets){
    const y=Math.max(2,Math.min(pageH-iconH-2,baseY+off));
    for(const x of lanes){
      const box={x,y,w:iconW,h:iconH};
      if(!used.some(u=>overlaps(box,u)))return {x,y};
    }
  }
  return {x:lanes[0],y:baseY};
}
function drawSideMarker(side,wordIds,rv){
  const z=zoomFor(side);
  if(!wordIds||!wordIds.length||!WORDS[side]?.length)return;
  const ws=wordIds.map(id=>WORDS[side][id]).filter(Boolean);
  if(!ws.length)return;
  const w=ws[0], pg=document.getElementById(`${side}-page-${w.page}`);if(!pg)return;
  const bbox=ws.reduce((b,x)=>[Math.min(b[0],x.bbox[0]),Math.min(b[1],x.bbox[1]),Math.max(b[2],x.bbox[2]),Math.max(b[3],x.bbox[3])],ws[0].bbox.slice());
  const icon=document.createElement('div');
  icon.className='reviewNote'+(isCleared(rv)?' cleared':'');
  icon.dataset.reviewId=rv.review_id;
  icon.dataset.side=side;
  const pos=markerPositionForBbox(pg,side,w.page,bbox);
  icon.style.left=pos.x*z+'px';
  icon.style.top=pos.y*z+'px';
  icon.onmouseenter=()=>showReviewHover(rv,side);
  icon.onmouseleave=clearAnchorHover;
  icon.onclick=e=>{e.stopPropagation();toggleNotePanel(pg,rv,side,bbox)};
  pg.appendChild(icon);
}
function panelPositionForBbox(pageEl,bbox,side){
  const z=zoomFor(side);
  const pageW=pageEl.clientWidth/z,pageH=pageEl.clientHeight/z,panelW=340/z,panelH=220/z,gap=8;
  let x;
  if(bbox[2]+gap+panelW<=pageW)x=bbox[2]+gap;
  else if(bbox[0]-gap-panelW>=0)x=bbox[0]-gap-panelW;
  else x=Math.max(2,Math.min(pageW-panelW-2,bbox[2]+gap));
  const y=Math.max(2,Math.min(pageH-panelH-2,bbox[1]));
  return {x,y};
}
function summarizeToolTrace(item){
  const trace=Array.isArray(item?.tool_trace)?item.tool_trace:[];
  const read=trace.filter(t=>t&&t.tool==='read_section').length;
  const search=trace.filter(t=>t&&t.tool==='search_markdown').length;
  return {total:trace.length,read,search};
}
function renderToolTrace(item){
  const s=summarizeToolTrace(item);
  if(!s.total)return `<div class="toolTraceBox"><span class="toolTracePill">Tool calls: 0</span></div>`;
  return `<div class="toolTraceBox"><span class="toolTracePill">Tool calls: ${s.total}</span>${s.read?`<span class="toolTracePill">read_section: ${s.read}</span>`:''}${s.search?`<span class="toolTracePill">search_markdown: ${s.search}</span>`:''}</div>`;
}
function renderNoteThread(rv){
  const comments=reviewComments(rv);
  if(!comments.length)return '<div class="noteComment">(No comment)</div>';
  return `<div class="noteThread">${comments.map(c=>`<div class="noteComment"><div>${escapeHtml(c.text||'')}</div>${c.created_at?`<div class="noteCommentMeta">${escapeHtml(String(c.created_at).slice(0,16).replace('T',' '))}${c.edited_at?` 쨌 <span class="editedMark">V Edited</span> ${escapeHtml(String(c.edited_at).slice(0,16).replace('T',' '))}`:''}</div>`:''}</div>`).join('')}</div>`;
}
function renderNotePanelHtml(rv){
  const actions=READ_ONLY?'':`<div class="noteReply"><textarea placeholder="Comment"></textarea><div class="noteReplyActions"><button type="button" data-act="saveComment">Comment</button><button type="button" data-act="cancelComment">Cancel</button></div></div><div class="noteActions"><button type="button" data-act="add">Comment</button><button type="button" data-act="toggle">${isCleared(rv)?'Reopen':'Clear'}</button><button type="button" class="noteDelete" data-act="delete">Delete</button></div>`;
  return `<button type="button" class="noteMin" data-act="min">_</button><div class="noteBody">${renderNoteThread(rv)}</div>${actions}`;
}
function toggleNotePanel(pageEl,rv,side,bbox){
  const z=zoomFor(side);
  const existing=pageEl.querySelector(`.reviewNotePanel[data-review-id="${rv.review_id}"]`);
  if(existing){existing.remove();return}
  document.querySelectorAll('.reviewNotePanel').forEach(e=>e.remove());
  const pos=panelPositionForBbox(pageEl,bbox,side);
  const panel=document.createElement('div');
  panel.className='reviewNotePanel'+(isCleared(rv)?' cleared':'');
  panel.dataset.reviewId=rv.review_id;
  panel.style.left=pos.x*z+'px';
  panel.style.top=pos.y*z+'px';
  panel.innerHTML=renderNotePanelHtml(rv);
  panel.addEventListener('mousedown',e=>e.stopPropagation());
  panel.addEventListener('click',e=>e.stopPropagation());
  panel.querySelector('[data-act="min"]').onclick=e=>{e.stopPropagation();panel.remove()};
  if(!READ_ONLY){
    panel.querySelector('[data-act="add"]').onclick=e=>{e.stopPropagation();showNoteReply(panel)};
    panel.querySelector('[data-act="cancelComment"]').onclick=e=>{e.stopPropagation();hideNoteReply(panel)};
    panel.querySelector('[data-act="saveComment"]').onclick=e=>{e.stopPropagation();saveNoteComment(panel,rv.review_id)};
    panel.querySelector('[data-act="toggle"]').onclick=e=>{e.stopPropagation();toggleReview(rv.review_id,isCleared(rv)?'open':'cleared')};
    panel.querySelector('[data-act="delete"]').onclick=e=>{e.stopPropagation();deleteReview(rv.review_id)};
  }
  makeDraggable(panel,panel,pageEl);
  pageEl.appendChild(panel);
  focusReview(rv,side);
}
function makeDraggable(panel,handle,pageEl){
  handle.addEventListener('mousedown',e=>{
    if(e.target.closest('button,textarea,.noteBody,.noteActions,.noteReply'))return;
    e.preventDefault();
    const startX=e.clientX,startY=e.clientY,startL=parseFloat(panel.style.left)||0,startT=parseFloat(panel.style.top)||0;
    const move=ev=>{
      const maxL=Math.max(0,pageEl.clientWidth-panel.offsetWidth-2),maxT=Math.max(0,pageEl.clientHeight-panel.offsetHeight-2);
      panel.style.left=Math.max(0,Math.min(maxL,startL+(ev.clientX-startX)))+'px';
      panel.style.top=Math.max(0,Math.min(maxT,startT+(ev.clientY-startY)))+'px';
    };
    const up=()=>{document.removeEventListener('mousemove',move);document.removeEventListener('mouseup',up)};
    document.addEventListener('mousemove',move);document.addEventListener('mouseup',up);
  });
}
function showNoteReply(panel){const box=panel.querySelector('.noteReply'),ta=box?.querySelector('textarea');if(!box||!ta)return;box.style.display='block';ta.value='';ta.focus()}
function hideNoteReply(panel){const box=panel.querySelector('.noteReply'),ta=box?.querySelector('textarea');if(!box||!ta)return;box.style.display='none';ta.value=''}
async function saveNoteComment(panel,id){const ta=panel.querySelector('.noteReply textarea');const text=(ta?.value||'').trim();if(!text)return;try{const rv=await postComment(id,text);panel.querySelector('.noteBody').innerHTML=renderNoteThread(rv);hideNoteReply(panel);renderReviewList(REVIEWS)}catch(e){toast('Failed to add comment')}}
async function postComment(id,text){
  const res=await fetch(`${apiBase()}/reviews/${id}/comments`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})});
  const updated=await res.json();
  if(!res.ok||updated.error)throw new Error(updated.error||'comment_failed');
  let merged=null;
  REVIEWS=REVIEWS.map(rv=>{
    if(rv.review_id!==id)return rv;
    const next={...rv,...updated,side:rv.side,anchor_run_id:rv.anchor_run_id,old_word_ids:rv.old_word_ids,new_word_ids:rv.new_word_ids,selection:rv.selection,floating:rv.floating};
    merged=next;
    return next;
  });
  if(merged){drawReviewMarkers(REVIEWS);return merged}
  return updated;
}
function showListReply(id){const row=document.querySelector(`.reviewRow[data-review-id="${id}"]`),box=row?.querySelector('.rvReply'),ta=box?.querySelector('textarea');if(!box||!ta)return;box.style.display='block';ta.value='';ta.focus()}
function hideListReply(id){const row=document.querySelector(`.reviewRow[data-review-id="${id}"]`),box=row?.querySelector('.rvReply'),ta=box?.querySelector('textarea');if(!box||!ta)return;box.style.display='none';ta.value=''}
function refreshOpenNotePanels(id,rv){document.querySelectorAll(`.reviewNotePanel[data-review-id="${id}"] .noteBody`).forEach(body=>{body.innerHTML=renderNoteThread(rv)})}
async function saveListComment(id){const row=document.querySelector(`.reviewRow[data-review-id="${id}"]`),ta=row?.querySelector('.rvReply textarea');const text=(ta?.value||'').trim();if(!text)return;try{const rv=await postComment(id,text);refreshOpenNotePanels(id,rv);renderReviewList(REVIEWS);focusReviewById(id)}catch(e){toast('Failed to add comment')}}
function askConfirm(message,opts={}){return new Promise(resolve=>{const dlg=$('#confirmDialog'),msg=$('#confirmMsg'),yes=$('#confirmYes'),no=$('#confirmNo');msg.textContent=message;yes.textContent=opts.yesText||'Yes';no.textContent=opts.noText||'No';dlg.style.display='flex';const done=v=>{dlg.style.display='none';yes.onclick=null;no.onclick=null;yes.textContent='Yes';no.textContent='No';resolve(v)};yes.onclick=()=>done(true);no.onclick=()=>done(false)})}
window.openChangeAiModal=function(changeId){
  const item=changeAiItemForId(changeId);
  if(!item){toast('No AI result for this change yet.');return}
  const verdict=normalizeChangeVerdict(item.verdict);
  const verdictMap={high:'High',medium:'Medium',low:'Low'};
  const modal=$('#changeAiModal'),title=$('#changeAiModalTitle'),body=$('#changeAiModalBody');
  if(!modal||!title||!body)return;
  title.textContent=`Change #${Number(changeId)} - ${verdictMap[verdict]||'Low'}`;
  const reason=String(item.reasoning||'').trim();
  const rec=verdict==='low'?'':String(item.recommended_comment||'').trim();
  body.innerHTML=`${reason?`<div class="changeModalSection"><strong>Reasoning</strong><div>${escapeHtml(reason)}</div></div>`:''}${rec?`<div class="changeModalSection"><strong>Suggested review</strong><div>${escapeHtml(rec)}</div></div>`:''}<div class="changeModalSection"><strong>Tool usage</strong>${renderToolTrace(item)}</div>${item.forwarded_review_id?`<div class="changeModalSection"><strong>Forwarded review</strong><div>${escapeHtml(item.forwarded_review_id)}</div></div>`:''}`;
  modal.style.display='flex';
}
window.closeChangeAiModal=function(){const modal=$('#changeAiModal');if(modal)modal.style.display='none'}
function reviewById(id){return (REVIEWS||[]).find(r=>r.review_id===id)||null}
function formatTs(ts){if(!ts)return '';return escapeHtml(String(ts).slice(0,19).replace('T',' '))}
function renderReviewDetailModal(){
  const rv=reviewById(REVIEW_DETAIL_MODAL_ID);
  const body=$('#reviewDetailBody'),title=$('#reviewDetailTitle');
  if(!body||!title)return;
  if(!rv){body.innerHTML='<div class="empty">Comment not found.</div>';return}
  const ai=aiItemForReview(rv.review_id);
  title.textContent=`Comment ${rv.review_id}`;
  const comments=reviewComments(rv);
  const thread=`<div class="reviewModalThread">${comments.map((c,idx)=>{const cid=c.comment_id||`legacy-${idx+1}`;const editing=(REVIEW_EDITING_COMMENT_ID===cid)&&!READ_ONLY;const meta=`<div class="reviewModalMeta">${formatTs(c.created_at)}${c.edited_at?` 쨌 <span class="editedMark">V Edited</span> ${formatTs(c.edited_at)}`:''}</div>`;if(editing){return `<div class="reviewModalComment">${meta}<textarea id="editCommentText">${escapeHtml(c.text||'')}</textarea><div class="reviewModalActions"><button type="button" onclick="saveReviewCommentEdit('${rv.review_id}','${cid}')">Save</button><button type="button" class="secondary" onclick="cancelReviewCommentEdit()">Cancel</button></div></div>`}const editBtn=(!READ_ONLY&&c.comment_id)?`<div class="reviewModalActions"><button type="button" onclick="startReviewCommentEdit('${cid}')">Edit</button></div>`:'';return `<div class="reviewModalComment">${meta}<div>${escapeHtml(c.text||'')}</div>${editBtn}</div>`}).join('')}</div>`;
  body.innerHTML=`<div class="changeModalSection"><strong>Status</strong><div>${escapeHtml(rv.status||'open')}</div></div><div class="changeModalSection"><strong>Selected text</strong><div>${escapeHtml(rv.text||'')}</div></div>${ai?`<div class="changeModalSection"><strong>AI opinion</strong>${renderAiReason(ai)}<div style="margin-top:6px">${renderToolTrace(ai)}</div></div>`:''}<div class="changeModalSection"><strong>Comment thread</strong>${thread}</div>`;
}
window.openReviewDetailModal=function(reviewId){
  REVIEW_DETAIL_MODAL_ID=reviewId;
  REVIEW_EDITING_COMMENT_ID=null;
  const modal=$('#reviewDetailModal');
  if(!modal)return;
  modal.style.display='flex';
  renderReviewDetailModal();
}
window.closeReviewDetailModal=function(){
  const modal=$('#reviewDetailModal');
  if(modal)modal.style.display='none';
  REVIEW_DETAIL_MODAL_ID=null;
  REVIEW_EDITING_COMMENT_ID=null;
}
window.startReviewCommentEdit=function(commentId){REVIEW_EDITING_COMMENT_ID=commentId;renderReviewDetailModal()}
window.cancelReviewCommentEdit=function(){REVIEW_EDITING_COMMENT_ID=null;renderReviewDetailModal()}
window.saveReviewCommentEdit=async function(reviewId,commentId){
  if(READ_ONLY){toast('Snapshot is read-only');return}
  const ta=$('#editCommentText');
  const text=String(ta?.value||'').trim();
  if(!text){toast('Comment text is required');return}
  try{
    const res=await fetch(`${apiBase()}/reviews/${reviewId}/comments/${commentId}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})});
    const data=await res.json();
    if(!res.ok||data.error){toast(data.error||'Edit failed');return}
    REVIEW_EDITING_COMMENT_ID=null;
    await loadReviews();
    focusReviewById(reviewId);
    if(REVIEW_DETAIL_MODAL_ID===reviewId)openReviewDetailModal(reviewId);
    toast('Comment updated');
  }catch(e){toast('Edit failed')}
}
function clearAnchorFocus(){document.querySelectorAll('.reviewAnchorFocus').forEach(e=>e.remove())}
function clearAnchorHover(){document.querySelectorAll('.reviewAnchorHover').forEach(e=>e.remove())}
function showAnchorHover(side,wordIds){clearAnchorHover();showAnchorFocus(side,wordIds,'reviewAnchorHover')}
function showAnchorFocus(side,wordIds,cls='reviewAnchorFocus'){
  const z=zoomFor(side);
  if(!wordIds||!wordIds.length||!WORDS[side]?.length)return;
  const byPageLine=new Map();
  wordIds.forEach(id=>{const w=WORDS[side][id];if(!w)return;const key=`${w.page}:${w.block??0}:${w.line??Math.round(w.bbox[1])}`;if(!byPageLine.has(key))byPageLine.set(key,{page:w.page,words:[]});byPageLine.get(key).words.push(w)});
  byPageLine.forEach(group=>{
    const pg=document.getElementById(`${side}-page-${group.page}`);if(!pg)return;
    const xs=[],ys=[];group.words.forEach(w=>{const[x0,y0,x1,y1]=w.bbox;xs.push(x0,x1);ys.push(y0,y1)});
    const x0=Math.min(...xs),x1=Math.max(...xs),y0=Math.min(...ys),y1=Math.max(...ys);
    const el=document.createElement('div');el.className=cls;
    el.style.left=x0*z+'px';el.style.top=y0*z+'px';
    el.style.width=Math.max(1,(x1-x0)*z)+'px';el.style.height=Math.max(2,(y1-y0)*z)+'px';
    pg.appendChild(el);
  });
}
function reviewHasLocation(rv,side){return side==='old'?(rv.old_word_ids||[]).length>0:(rv.new_word_ids||[]).length>0}
function reviewSortRank(rv){if(isCleared(rv))return 2;return reviewHasLocation(rv,REVIEW_SIDE)?0:1}
function filteredSortedReviews(reviews){
  let out=[...(reviews||[])].filter(rv=>reviewBelongsToSide(rv,REVIEW_SIDE));
  out=out.filter(rv=>{
    const ai=aiItemForReview(rv.review_id), matched=reviewHasLocation(rv,REVIEW_SIDE);
    if(REVIEW_FILTERS.status==='open'&&isCleared(rv))return false;
    if(REVIEW_FILTERS.status==='cleared'&&!isCleared(rv))return false;
    if(REVIEW_FILTERS.ai==='checked'&&!ai)return false;
    if(REVIEW_FILTERS.ai==='unchecked'&&ai)return false;
    if(REVIEW_FILTERS.match==='matched'&&!matched)return false;
    if(REVIEW_FILTERS.match==='unmatched'&&matched)return false;
    return true;
  });
  out.sort((a,b)=>{
    if(REVIEW_FILTERS.sort==='comments_desc')return reviewComments(b).length-reviewComments(a).length||reviewSortRank(a)-reviewSortRank(b);
    if(REVIEW_FILTERS.sort==='comments_asc')return reviewComments(a).length-reviewComments(b).length||reviewSortRank(a)-reviewSortRank(b);
    return reviewSortRank(a)-reviewSortRank(b)||String(b.updated_at||'').localeCompare(String(a.updated_at||''));
  });
  return out;
}
window.setReviewFilter=function(key,value){REVIEW_FILTERS[key]=value;renderReviewList(REVIEWS)}
window.resetReviewFilters=function(){REVIEW_FILTERS={status:'all',ai:'all',match:'all',sort:'default'};const vals={filterStatus:'all',filterAi:'all',filterMatch:'all',filterSort:'default'};Object.keys(vals).forEach(id=>{const el=document.getElementById(id);if(el)el.value=vals[id]});renderReviewList(REVIEWS)}
function updateReviewSubtabs(){if(!$('#reviewPrevTab'))return;$('#reviewPrevTab').classList.toggle('active',REVIEW_SIDE==='old');$('#reviewCurrentTab').classList.toggle('active',REVIEW_SIDE==='new');$('#aiBar').style.display=(isDiff()&&REVIEW_SIDE==='old')?'flex':'none'}
window.setReviewSide=function(side){REVIEW_SIDE=side;updateReviewSubtabs();renderReviewList(REVIEWS)}
function aiItemForReview(id){return (AI_ASSESSMENT.items||[]).find(x=>x.review_id===id)}
function changeAiItemForId(id){return (CHANGE_AI_ASSESSMENT.items||[]).find(x=>Number(x.change_id)===Number(id))}
function isReviewAiDone(id){const it=aiItemForReview(id);return !!(it&&it.status==='done'&&String(it.verdict||'').trim())}
function isChangeAiDone(id){const it=changeAiItemForId(id);return !!(it&&it.status==='done'&&String(it.verdict||'').trim())}
function decisionToReviewStatus(decision){return String(decision||'').toLowerCase()==='cleared'?'cleared':'open'}
function renderChangeAiReasonForList(changeId,item){
  const running=CHANGE_AI_RUNNING_IDS.has(Number(changeId));
  if(!item&&!running)return '';
  if(running&&!item)return `<div class="aiReason"><div><strong>AI running...</strong></div></div>`;
  const verdict=normalizeChangeVerdict(item.verdict);
  const verdictMap={high:'High',medium:'Medium',low:'Low'};
  let reasoningText=String(item.reasoning||'').replace(/\s+/g,' ').trim();
  if(reasoningText.length>160)reasoningText=reasoningText.slice(0,160).trim()+'...';
  const rec=verdict==='low'?'':String(item.recommended_comment||'').trim();
  const recLine=rec?`<div><strong>Suggested review:</strong> ${escapeHtml(trim(rec,180))}</div>`:'';
  const forwarded=item.forwarded_review_id?`<div><strong>Forwarded:</strong> ${escapeHtml(item.forwarded_review_id)}</div>`:'';
  return `<div class="aiReason" onclick="event.stopPropagation();openChangeAiModal(${Number(changeId)})" title="Open full AI result"><div class="riskBadge"><span class="riskDot ${escapeHtml(verdict)}"></span><strong>${escapeHtml(verdictMap[verdict]||'Low')}</strong></div>${running?`<div><strong>AI running...</strong></div>`:''}${reasoningText?`<div>${escapeHtml(reasoningText)}</div>`:''}${recLine}${forwarded}</div>`;
}
function renderReviewSummary(rv){const comments=reviewComments(rv);const last=comments.length?comments[comments.length-1]:null;const more=Math.max(0,comments.length-1);const text=last?.text||rv.comment||'(No comment)';return `<div class="rvSummary"><div class="rvSummaryText">${escapeHtml(text)}</div>${more?`<span class="rvMoreCount">+${more}</span>`:''}</div>`}
function renderReviewList(reviews){const visible=filteredSortedReviews(reviews);const total=(reviews||[]).filter(rv=>reviewBelongsToSide(rv,REVIEW_SIDE)).length;const pane=$('#reviewPane');if(!visible.length){pane.innerHTML=`<div class="reviewPlaceholder">${total?'No comments match the current filters.':'No '+(REVIEW_SIDE==='old'?'previous':'current')+' comments yet. Drag text on the report to add one.'}</div>`;return}pane.innerHTML=visible.map(rv=>{const ai=aiItemForReview(rv.review_id);const isRunning=REVIEW_AI_RUNNING_IDS.has(rv.review_id);const oldActions=REVIEW_SIDE==='old'?`<button onclick="event.stopPropagation();moveReviewToCurrent('${rv.review_id}')">Forward</button><button onclick="event.stopPropagation();runReviewAiAssessment('${rv.review_id}')">AI</button>`:'';const accept=(ai&&String(ai.verdict||'').toLowerCase()==='cleared')?`<button onclick="event.stopPropagation();saveAssessmentDecision('${rv.review_id}','${escapeHtml(ai.verdict||'unclear')}')">Accept</button>`:'';const actions=READ_ONLY?'':`<div class="rvActions"><button onclick="event.stopPropagation();addComment('${rv.review_id}')">Comment</button><button onclick="event.stopPropagation();toggleReview('${rv.review_id}','${isCleared(rv)?'open':'cleared'}')">${isCleared(rv)?'Reopen':'Clear'}</button>${oldActions}${accept}<button class="noteDelete" onclick="event.stopPropagation();deleteReview('${rv.review_id}')">Del</button></div><div class="rvReply" onclick="event.stopPropagation()"><textarea placeholder="Comment"></textarea><div class="rvReplyActions"><button onclick="event.stopPropagation();saveListComment('${rv.review_id}')">Comment</button><button onclick="event.stopPropagation();hideListReply('${rv.review_id}')">Cancel</button></div></div>`;const loc=reviewHasLocation(rv,REVIEW_SIDE)?`p.${firstPage(REVIEW_SIDE,rv)}`:'Floating';return `<div class="reviewRow ${isCleared(rv)?'cleared':'open'}" data-review-id="${rv.review_id}" onclick="openReviewDetailModal('${rv.review_id}');focusReviewById('${rv.review_id}')"><div class="meta">${REVIEW_SIDE==='old'?'previous':'current'} ${loc} 쨌 ${reviewComments(rv).length} comment${reviewComments(rv).length===1?'':'s'} 쨌 ${isCleared(rv)?'cleared':'open'}${isRunning?' 쨌 AI running...':''}</div>${renderReviewSummary(rv)}${renderAiReason(ai)}${actions}</div>`}).join('')}
function firstPage(side,rv){const ids=rv[side+'_word_ids']||[];if(!ids.length||!WORDS[side]?.length)return '-';const w=WORDS[side][ids[0]];return w?w.page:'-'}
function focusReview(rv,displaySide){
  clearAnchorFocus();
  const primarySide=displaySide||(reviewBelongsToSide(rv,REVIEW_SIDE)?REVIEW_SIDE:(rv.old_word_ids?.length?'old':'new'));
  const newIds=reviewWordIdsForSide(rv,'new');
  if(newIds.length){if(primarySide==='new')showAnchorFocus('new',newIds);scrollWordIdsIntoPane('new',newIds)}
  else scrollReviewChangeFallback('new',rv);
  if(isDiff()){
    const oldIds=reviewWordIdsForSide(rv,'old');
    if(oldIds.length){if(primarySide==='old')showAnchorFocus('old',oldIds);scrollWordIdsIntoPane('old',oldIds)}
    else scrollReviewChangeFallback('old',rv);
  }
  document.querySelectorAll('.review,.reviewNote').forEach(e=>e.classList.toggle('active',e.dataset.reviewId===rv.review_id&&(!e.classList.contains('reviewNote')||e.dataset.side===primarySide)));
  document.querySelectorAll('.reviewRow').forEach(e=>e.classList.toggle('active',e.dataset.reviewId===rv.review_id));
}
window.focusReviewById=function(id){const matches=REVIEWS.filter(r=>r.review_id===id);const rv=matches.find(r=>reviewBelongsToSide(r,REVIEW_SIDE))||matches[0];if(rv)focusReview(rv,reviewBelongsToSide(rv,REVIEW_SIDE)?REVIEW_SIDE:undefined)}
window.toggleReview=async function(id,status){if(READ_ONLY){toast('Snapshot is read-only');return}try{await fetch(`${apiBase()}/reviews/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({status})});await loadReviews()}catch(e){toast('Failed to update status')}}
window.addComment=function(id){if(READ_ONLY){toast('Snapshot is read-only');return}showListReply(id)}
window.deleteReview=async function(id){if(READ_ONLY){toast('Snapshot is read-only');return}try{await fetch(`${apiBase()}/reviews/${id}`,{method:'DELETE'});await loadReviews();clearAnchorFocus()}catch(e){toast('Failed to delete review')}}
window.moveReviewToCurrent=async function(id){if(READ_ONLY){toast('Snapshot is read-only');return}const ok=await askConfirm('Move this previous review to Current?');if(!ok)return;try{const res=await fetch(`${apiBase()}/migrate`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({review_id:id})});const data=await res.json();if(!res.ok||data.error){toast(data.error||'Move failed');return}REVIEW_SIDE='new';await loadReviews();updateReviewSubtabs();const movedId=data?.review?.review_id||id;focusReviewById(movedId);toast(data.already_current?'Already in Current':'Copied to Current')}catch(e){toast('Move failed')}}
window.moveOpenPreviousReviewsToCurrent=async function(){if(READ_ONLY){toast('Snapshot is read-only');return}const visible=filteredSortedReviews(REVIEWS||[]);const targets=[...new Map(visible.filter(rv=>reviewBelongsToSide(rv,'old')&&!isCleared(rv)).map(rv=>[rv.review_id,rv])).values()];if(!targets.length){toast('No open previous comments to forward in current filter.');return}const ok=await askConfirm(`Forward ${targets.length} open previous comment${targets.length===1?'':'s'} to Current?`);if(!ok)return;const btn=$('#moveOpenBtn');if(btn){btn.disabled=true;btn.textContent='Forwarding...'}let moved=0,failed=0,lastError='';try{for(const rv of targets){try{const res=await fetch(`${apiBase()}/migrate`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({review_id:rv.review_id})});const data=await res.json();if(!res.ok||data.error){failed++;lastError=data.error||String(res.status)}else moved++}catch(e){failed++;lastError=e.message||'request failed'}}REVIEW_SIDE='new';await loadReviews();updateReviewSubtabs();toast(`Forwarded ${moved}${failed?`, ${failed} failed${lastError?`: ${lastError}`:''}`:''}`)}finally{if(btn){btn.disabled=false;btn.textContent='Forward All'}}}
window.runAiAssessment=async function(){
  if(READ_ONLY){toast('Snapshot is read-only');return}
  if(!isDiff()){toast('Compare reports before running AI.');return}
  if(REVIEW_AI_ABORT_CONTROLLER){
    REVIEW_AI_ABORT_CONTROLLER.abort();
    return;
  }
  const visible=filteredSortedReviews(REVIEWS||[]);
  const reviewIds=[...new Set(visible.map(rv=>rv.review_id).filter(Boolean))];
  if(!reviewIds.length){toast('No filtered comments to run AI on.');return}
  const doneIds=reviewIds.filter(id=>isReviewAiDone(id));
  let skipExisting=true;
  if(doneIds.length){
    skipExisting=await askConfirm(
      `Skip completed items? (${doneIds.length})`,
      {yesText:'Yes',noText:'No'}
    );
  }
  const targetIds=skipExisting?reviewIds.filter(id=>!isReviewAiDone(id)):reviewIds;
  if(!targetIds.length){toast('All filtered comments are already AI processed.');return}
  REVIEW_AI_ABORT_CONTROLLER=new AbortController();
  updateAiButtons();
  setReviewAiRunningIds(targetIds);
  setAiRunState({running:true,total:targetIds.length,done:0,failed:0,message:`AI running... 0/${targetIds.length}`});
  try{
    const res=await fetch(`${apiBase()}/assess`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({review_ids:targetIds,skip_existing:skipExisting}),signal:REVIEW_AI_ABORT_CONTROLLER.signal});
    const data=await res.json();
    if(!res.ok||data.error){throw new Error(data.error||'AI check failed')}
    AI_ASSESSMENT=data;
    renderReviewList(REVIEWS);
    const targetSet=new Set(targetIds);
    const done=(data.items||[]).filter(i=>targetSet.has(i.review_id)&&i.status==='done').length;
    const failed=(data.items||[]).filter(i=>targetSet.has(i.review_id)&&i.status==='error').length;
    setAiRunState({running:false,done,failed,message:`AI complete: ${done}/${targetIds.length}${failed?` (${failed} failed)`:''}`});
    toast(`AI check complete${failed?` (${failed} failed)`:''}`);
  }catch(e){
    if(e.name==='AbortError'){
      setAiRunState({running:false,done:0,failed:0,message:'AI stopped'});
      toast('AI stopped');
    }else{
      setAiRunState({running:false,done:0,failed:targetIds.length,message:'AI failed'});
      toast(e.message||'AI check failed');
    }
  }finally{
    REVIEW_AI_ABORT_CONTROLLER=null;
    setReviewAiRunningIds([]);
    updateAiButtons();
  }
}
window.runReviewAiAssessment=async function(id){
  if(READ_ONLY){toast('Snapshot is read-only');return}
  setReviewAiRunningIds([id]);
  setAiRunState({running:true,total:1,done:0,failed:0,message:'AI running... 0/1'});
  try{
    const res=await fetch(`${apiBase()}/assess/${id}`,{method:'POST'});
    const data=await res.json();
    if(!res.ok||data.error){
      setAiRunState({running:false,done:0,failed:1,message:'AI failed (0/1)'});
      toast(data.error||'AI check failed');
      return;
    }
    AI_ASSESSMENT=data;
    renderReviewList(REVIEWS);
    focusReviewById(id);
    setAiRunState({running:false,done:1,failed:0,message:'AI complete (1/1)'});
    toast('AI check complete');
  }catch(e){
    setAiRunState({running:false,done:0,failed:1,message:'AI failed (0/1)'});
    toast('AI check failed');
  }finally{
    setReviewAiRunningIds([]);
  }
}
window.runSingleChangeAi=async function(changeId){
  if(READ_ONLY){toast('Snapshot is read-only');return}
  if(!isDiff()){toast('Compare reports before running AI.');return}
  setChangeAiRunningIds([changeId]);
  setChangeAiRunState({running:true,total:1,done:0,failed:0,message:'AI running... 0/1'});
  try{
    const res=await fetch(`${apiBase()}/change-assess/${Number(changeId)}`,{method:'POST'});
    const data=await res.json();
    if(!res.ok||data.error){
      setChangeAiRunState({running:false,done:0,failed:1,message:'AI failed (0/1)'});
      toast(data.error||'Change AI failed');
      return;
    }
    CHANGE_AI_ASSESSMENT=data;
    drawChanges(DATA);
    setChangeAiRunState({running:false,done:1,failed:0,message:'AI complete (1/1)'});
    toast('Change AI complete');
  }catch(e){
    setChangeAiRunState({running:false,done:0,failed:1,message:'AI failed (0/1)'});
    toast('Change AI failed');
  }finally{
    setChangeAiRunningIds([]);
  }
}
window.runChangeAiOnAll=async function(){
  if(READ_ONLY){toast('Snapshot is read-only');return}
  if(!isDiff()){toast('Compare reports before running AI.');return}
  if(CHANGE_AI_ABORT_CONTROLLER){
    CHANGE_AI_ABORT_CONTROLLER.abort();
    return;
  }
  const changes=DATA?.changes||[];
  const changeIds=[...new Set(changes.map(c=>Number(c.id)).filter(n=>Number.isFinite(n)))];
  if(!changeIds.length){toast('No changes to run AI on.');return}
  const doneIds=changeIds.filter(id=>isChangeAiDone(id));
  let skipExisting=true;
  if(doneIds.length){
    skipExisting=await askConfirm(
      `Skip completed items? (${doneIds.length})`,
      {yesText:'Yes',noText:'No'}
    );
  }
  const targetIds=skipExisting?changeIds.filter(id=>!isChangeAiDone(id)):changeIds;
  if(!targetIds.length){toast('All changes are already AI processed.');return}
  CHANGE_AI_ABORT_CONTROLLER=new AbortController();
  updateAiButtons();
  setChangeAiRunningIds(targetIds);
  setChangeAiRunState({running:true,total:targetIds.length,done:0,failed:0,message:`AI running... 0/${targetIds.length}`});
  try{
    const grpRes=await fetch(`${apiBase()}/change-assess/groups`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({change_ids:targetIds}),signal:CHANGE_AI_ABORT_CONTROLLER.signal});
    const grpData=await grpRes.json();
    if(!grpRes.ok||grpData.error)throw new Error(grpData.error||'Change AI grouping failed');
    const groups=(grpData.groups||[]).filter(g=>Array.isArray(g.change_ids)&&g.change_ids.length);
    const targetSet=new Set(targetIds);
    let done=0,failed=0;
    for(let i=0;i<groups.length;i++){
      const group=groups[i];
      setChangeAiRunState({running:true,done,failed,message:`AI running... ${done}/${targetIds.length} (group ${i+1}/${groups.length})`});
      const res=await fetch(`${apiBase()}/change-assess`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({change_ids:group.change_ids}),signal:CHANGE_AI_ABORT_CONTROLLER.signal});
      const data=await res.json();
      if(!res.ok||data.error)throw new Error(data.error||'Change AI failed');
      mergeChangeAssessmentItems(data.items||[]);
      drawChanges(DATA);
      done=(CHANGE_AI_ASSESSMENT.items||[]).filter(i=>targetSet.has(Number(i.change_id))&&i.status==='done').length;
      failed=(CHANGE_AI_ASSESSMENT.items||[]).filter(i=>targetSet.has(Number(i.change_id))&&i.status==='error').length;
    }
    setChangeAiRunState({running:false,done,failed,message:`AI complete: ${done}/${targetIds.length}${failed?` (${failed} failed)`:''}`});
    toast(`Change AI complete${failed?` (${failed} failed)`:''}`);
  }catch(e){
    if(e.name==='AbortError'){
      setChangeAiRunState({running:false,done:0,failed:0,message:'AI stopped'});
      toast('Change AI stopped');
    }else{
      setChangeAiRunState({running:false,done:0,failed:targetIds.length,message:'AI failed'});
      toast(e.message||'Change AI failed');
    }
  }finally{
    CHANGE_AI_ABORT_CONTROLLER=null;
    setChangeAiRunningIds([]);
    updateAiButtons();
  }
}
window.forwardChangeAi=async function(changeId){
  if(READ_ONLY){toast('Snapshot is read-only');return}
  try{
    const res=await fetch(`${apiBase()}/change-assess/${Number(changeId)}/forward`,{method:'POST'});
    const data=await res.json();
    if(!res.ok||data.error){toast(data.error||'Forward failed');return}
    await loadReviews();
    await loadChangeAssessment();
    toast(data.already_forwarded?'Already forwarded':'Forwarded to comments');
  }catch(e){toast('Forward failed')}
}
window.forwardAllChangeAi=async function(){
  if(READ_ONLY){toast('Snapshot is read-only');return}
  const targets=(CHANGE_AI_ASSESSMENT.items||[]).filter(i=>{const v=normalizeChangeVerdict(i.verdict);const hasComment=String(i.recommended_comment||'').trim().length>0;return (v==='high'||v==='medium')&&hasComment&&!i.forwarded_review_id}).map(i=>Number(i.change_id)).filter(n=>Number.isFinite(n));
  if(!targets.length){toast('No high/medium risk changes to forward.');return}
  const ok=await askConfirm(`Forward ${targets.length} AI suggested review${targets.length===1?'':'s'}?`);
  if(!ok)return;
  const btn=$('#forwardAllChangesBtn');
  if(btn){btn.disabled=true;btn.textContent='Forwarding...'}
  let moved=0,failed=0,lastError='';
  try{
    for(const changeId of targets){
      try{
        const res=await fetch(`${apiBase()}/change-assess/${changeId}/forward`,{method:'POST'});
        const data=await res.json();
        if(!res.ok||data.error){failed++;lastError=data.error||String(res.status)}
        else moved++;
      }catch(e){failed++;lastError=e.message||'request failed'}
    }
    await loadReviews();
    await loadChangeAssessment();
    toast(`Forwarded ${moved}${failed?`, ${failed} failed${lastError?`: ${lastError}`:''}`:''}`);
  }finally{
    if(btn){btn.disabled=false;btn.textContent='Forward All'}
  }
}
window.saveAssessmentDecision=async function(id,decision){
  if(READ_ONLY){toast('Snapshot is read-only');return}
  try{
    const assessRes=await fetch(`${apiBase()}/assess/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({human_decision:decision})});
    const assessData=await assessRes.json();
    if(!assessRes.ok||assessData.error){toast(assessData.error||'Failed to save decision');return}

    const status=decisionToReviewStatus(decision);
    const reviewRes=await fetch(`${apiBase()}/reviews/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({status})});
    const reviewData=await reviewRes.json();
    if(!reviewRes.ok||reviewData.error){toast(reviewData.error||'Decision saved but status update failed');return}

    const item=aiItemForReview(id);
    if(item){item.human_decision=decision;item.human_updated_at=assessData.human_updated_at}
    await loadReviews();
    focusReviewById(id);
    toast(`Decision saved (${verdictLabel(decision)})`);
  }catch(e){
    toast('Failed to save decision');
  }
}
window.exportPdf=function(side){if(!DATA)return;if(!REVIEWS.length){toast('No saved comments');return}window.open(`${apiBase()}/export/${side}`,'_blank')}
window.saveWorkspace=async function(side='new'){
  if(!DOC_ID||!RUN_ID){toast('Open a workspace first');return}
  try{
    const payload={title:titleFromFilename(filenameForSide(side)),overwrite_existing:true};
    const res=await fetch(`/api/documents/${DOC_ID}/runs/${RUN_ID}/save-file/${side}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const data=await res.json();
    if(!res.ok||data.error){toast(data.error==='duplicate title'?'Name already exists. Use Save As':(data.error||'Save failed'));return}
    toast(data.overwritten?'File updated in workspace':'Saved to workspace files');
  }catch(e){toast('Save failed')}
}
window.saveAsWorkspace=async function(side='new'){
  if(!DOC_ID){toast('Open a workspace first');return}
  const defaultTitle=`${titleFromFilename(filenameForSide(side))} copy`;
  const name=prompt('Save file as',defaultTitle);
  if(name===null)return;
  const title=String(name||'').trim();
  if(!title){toast('Name is required');return}
  try{
    const res=await fetch(`/api/documents/${DOC_ID}/runs/${RUN_ID}/save-file/${side}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title})});
    const data=await res.json();
    if(!res.ok||data.error){toast(data.error==='duplicate title'?'Name already exists':(data.error||'Save As failed'));return}
    toast('Saved as new file in workspace');
  }catch(e){toast('Save As failed')}
}

async function bootstrapFromQuery(){
  const docId=new URLSearchParams(location.search).get('doc');
  try{
    setSidebarCollapsed(false);
    if(docId)await openWorkspaceSession(docId);
    else{
      const ws=await ensureDefaultWorkspace();
      await openWorkspaceSession(ws.workspace_id);
    }
    setBusy(false,'Ready');
  }catch(e){
    try{
      const ws=await ensureDefaultWorkspace();
      await openWorkspaceSession(ws.workspace_id);
      setBusy(false,'Ready');
    }catch(_){toast('Failed to open workspace')}
  }
}
bootstrapFromQuery();
