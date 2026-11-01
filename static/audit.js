const $=id=>document.getElementById(id);
let auditStatus=null, calibrationData={}, episodes=[];

function todayISO(){
  const d=new Date();
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;
}
function esc(v){return String(v??"").replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[m]));}
function fmtPt(v){
  if(!v)return "—";
  const d=new Date(v);
  return d.toLocaleString("en-CA",{timeZone:"America/Vancouver",year:"numeric",month:"short",day:"2-digit",hour:"2-digit",minute:"2-digit"});
}
function roleCanEditCalibration(){return auditStatus?.access?.role==="admin";}
function roleCanEditDeviation(){return ["admin","operator"].includes(auditStatus?.access?.role);}
function roleIsAdmin(){return auditStatus?.access?.role==="admin";}

async function loadStatus(){
  const r=await fetch("/api/audit/status",{cache:"no-store"});
  auditStatus=await r.json();
  $("auditTimezone").textContent=auditStatus.timezone||"America/Vancouver";
  $("auditClock").textContent="Server: "+fmtPt(auditStatus.server_utc);
  $("auditRetention").textContent=(auditStatus.retention?.retention_years||3)+" YEARS";
  $("auditBackup").textContent=auditStatus.backup?.available?"AVAILABLE":"NONE";
  $("auditBackupTime").textContent=auditStatus.backup?.last_backup_pacific?fmtPt(auditStatus.backup.last_backup_pacific):"No backup created yet";
  $("auditRole").textContent=(auditStatus.access?.role||"read-only").toUpperCase();
  $("auditUser").textContent=auditStatus.access?.logged_in?`User: ${auditStatus.access.username}`:"Login in Settings to edit";

  const opts=(auditStatus.devices||[]).map(d=>`<option value="${esc(d.logger_sn)}">${esc(d.name)} — ${esc(d.logger_sn)}</option>`).join("");
  $("reportLogger").innerHTML='<option value="ALL">All Loggers</option>'+opts;
  $("calLogger").innerHTML=opts;

  const cfg=auditStatus.retention||{};
  $("retentionYears").value=String(cfg.retention_years||3);
  $("backupEnabled").value=String(cfg.backup_enabled!==false);
  $("backupTime").value=cfg.backup_time||"02:15";
  $("backupKeep").value=cfg.backup_keep||30;

  $("saveCalibrationBtn").disabled=!roleCanEditCalibration();
  $("saveDeviationBtn").disabled=!roleCanEditDeviation();
  ["saveAuditConfigBtn","backupNowBtn","downloadBackupBtn"].forEach(id=>$(id).disabled=!roleIsAdmin());
}

async function loadCalibrations(){
  const r=await fetch("/api/audit/calibrations",{cache:"no-store"}),x=await r.json();
  calibrationData=x.records||{};
  let rows=[];
  Object.entries(calibrationData).forEach(([sn,list])=>(list||[]).forEach(rec=>rows.push(rec)));
  rows.sort((a,b)=>String(b.calibration_date||"").localeCompare(String(a.calibration_date||"")));
  $("calibrationRows").innerHTML=rows.length?rows.slice(0,100).map(r=>`<tr>
    <td>${esc(r.calibration_date)}</td><td>${esc(r.name)}<br><small>${esc(r.logger_sn)}</small></td>
    <td>${Number(r.temperature_offset||0).toFixed(2)} °C</td><td>${Number(r.humidity_offset||0).toFixed(2)} %RH</td>
    <td>${esc(r.reference_instrument)}<br><small>${esc(r.reference_serial||"")}</small></td>
    <td>${esc(r.certificate_id||"—")}</td><td>${esc(r.next_due_date||"—")}</td><td>${esc(r.performed_by||"—")}</td></tr>`).join("")
    :'<tr><td colspan="8">No calibration records entered yet.</td></tr>';
}

async function loadEpisodes(){
  const r=await fetch("/api/audit/episodes?days=365",{cache:"no-store"}),x=await r.json();
  episodes=x.episodes||[];
  $("devEpisode").innerHTML=episodes.length?episodes.map(ep=>{
    const start=fmtPt(ep.start_utc);
    const status=ep.status||"";
    return `<option value="${esc(ep.id)}">${esc(ep.name)} · ${start} · ${esc(status)}</option>`;
  }).join(""):'<option value="">No alarm episodes available</option>';
  if(episodes.length)populateDeviationFromEpisode();
}
function populateDeviationFromEpisode(){
  const ep=episodes.find(e=>String(e.id)===$("devEpisode").value);
  const d=ep?.deviation||{};
  $("devStatus").value=d.status||"OPEN";
  $("devDepartment").value=d.department||"Maintenance";
  $("devResponsible").value=d.responsible_person||"";
  $("devReviewedBy").value=d.reviewed_by||"";
  $("devReviewDate").value=d.review_date||"";
  $("devRootCause").value=d.root_cause||"";
  $("devCorrective").value=d.corrective_action||"";
  $("devDisposition").value=d.product_disposition||"";
  $("devNotes").value=d.notes||"";
}
$("devEpisode").addEventListener("change",populateDeviationFromEpisode);

async function loadAuditTrail(){
  const r=await fetch("/api/audit-trail?limit=500",{cache:"no-store"}),x=await r.json(),rows=x.records||[];
  $("auditRows").innerHTML=rows.length?rows.slice().reverse().map(r=>`<tr>
    <td>${esc(fmtPt(r.time_utc))}</td><td>${esc(r.category||"")}</td><td><b>${esc(r.action||"")}</b></td>
    <td>${esc(r.actor||"—")}<br><small>${esc(r.role||"")}</small></td>
    <td>${esc(r.name||r.logger_sn||"—")}<br><small>${esc(r.logger_sn||"")}</small></td>
    <td>${esc(r.detail||"")}</td></tr>`).join("")
    :'<tr><td colspan="6">No audit records available.</td></tr>';
}

function downloadAuditReport(){
  const date=$("reportDate").value, sn=$("reportLogger").value;
  if(!date){alert("Select a report date.");return;}
  window.location.href=`/api/audit-report.pdf?logger_sn=${encodeURIComponent(sn)}&date=${encodeURIComponent(date)}`;
}

async function saveCalibration(){
  if(!roleCanEditCalibration()){alert("Administrator login is required.");return;}
  const sn=$("calLogger").value;
  const payload={
    calibration_date:$("calDate").value,
    next_due_date:$("calDue").value,
    reference_instrument:$("calReference").value.trim(),
    reference_serial:$("calReferenceSerial").value.trim(),
    certificate_id:$("calCertificate").value.trim(),
    temperature_offset:Number($("calTempOffset").value||0),
    humidity_offset:Number($("calRhOffset").value||0),
    performed_by:$("calPerformedBy").value.trim(),
    notes:$("calNotes").value.trim()
  };
  $("calMsg").textContent="Saving calibration record…";
  const r=await fetch(`/api/admin/audit/calibration/${encodeURIComponent(sn)}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
  const x=await r.json().catch(()=>({}));
  if(!r.ok){$("calMsg").innerHTML=`<span class="bad">${esc(x.detail||"Save failed")}</span>`;return;}
  $("calMsg").innerHTML='<span class="good">Calibration record saved and offsets applied.</span>';
  await loadCalibrations(); await loadAuditTrail();
}

async function saveDeviation(){
  if(!roleCanEditDeviation()){alert("Admin or Operator login is required.");return;}
  const payload={
    episode_id:$("devEpisode").value,
    status:$("devStatus").value,
    department:$("devDepartment").value,
    responsible_person:$("devResponsible").value.trim(),
    reviewed_by:$("devReviewedBy").value.trim(),
    review_date:$("devReviewDate").value,
    root_cause:$("devRootCause").value.trim(),
    corrective_action:$("devCorrective").value.trim(),
    product_disposition:$("devDisposition").value.trim(),
    notes:$("devNotes").value.trim()
  };
  $("devMsg").textContent="Saving deviation record…";
  const r=await fetch("/api/audit/deviation",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
  const x=await r.json().catch(()=>({}));
  if(!r.ok){$("devMsg").innerHTML=`<span class="bad">${esc(x.detail||"Save failed")}</span>`;return;}
  $("devMsg").innerHTML='<span class="good">Deviation / corrective action saved to the audit trail.</span>';
  await loadEpisodes(); await loadAuditTrail();
}

async function saveAuditConfig(){
  if(!roleIsAdmin()){alert("Administrator login is required.");return;}
  const payload={
    retention_years:Number($("retentionYears").value),
    backup_enabled:$("backupEnabled").value==="true",
    backup_time:$("backupTime").value,
    backup_keep:Number($("backupKeep").value)
  };
  const r=await fetch("/api/admin/audit/config",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
  const x=await r.json().catch(()=>({}));
  if(!r.ok){$("backupMsg").innerHTML=`<span class="bad">${esc(x.detail||"Save failed")}</span>`;return;}
  $("backupMsg").innerHTML='<span class="good">Retention and backup policy saved.</span>';
  await loadStatus(); await loadAuditTrail();
}
async function backupNow(){
  if(!roleIsAdmin()){alert("Administrator login is required.");return;}
  $("backupMsg").textContent="Creating backup…";
  const r=await fetch("/api/admin/audit/backup-now",{method:"POST"}),x=await r.json().catch(()=>({}));
  if(!r.ok){$("backupMsg").innerHTML=`<span class="bad">${esc(x.detail||"Backup failed")}</span>`;return;}
  $("backupMsg").innerHTML=`<span class="good">Backup created: ${esc(x.filename)}</span>`;
  await loadStatus(); await loadAuditTrail();
}
function downloadLatestBackup(){
  if(!roleIsAdmin()){alert("Administrator login is required.");return;}
  window.location.href="/api/admin/audit/latest-backup";
}

async function loadAll(){
  await loadStatus();
  await Promise.all([loadCalibrations(),loadEpisodes(),loadAuditTrail()]);
}

$("reportDate").value=todayISO();
$("calDate").value=todayISO();
$("devReviewDate").value=todayISO();
loadAll();
setInterval(loadAuditTrail,30000);
