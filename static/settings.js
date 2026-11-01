const $=id=>document.getElementById(id);
let currentRole=null;
let currentUsername=null;
let deviceMap={};

async function status(){
  const r=await fetch("/api/admin/status",{cache:"no-store"});
  const x=await r.json();
  if(!x.configured){
    $("loginMsg").innerHTML='<span class="bad">Admin password is not configured. Run SET_ADMIN_PASSWORD.bat on the mini-PC.</span>';
  }
  if(x.logged_in){
    showSettings(x.username,x.role);
    await loadConfig();
  }
}

async function login(){
  $("loginMsg").textContent="Signing in…";
  const r=await fetch("/api/admin/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:$("adminUser").value,password:$("adminPass").value})});
  const x=await r.json().catch(()=>({}));
  if(!r.ok){$("loginMsg").innerHTML='<span class="bad">'+(x.detail||"Login failed")+'</span>';return;}
  $("adminPass").value="";
  showSettings(x.username,x.role);
  await loadConfig();
}

function showSettings(user,role){
  currentUsername=user; currentRole=role;
  $("loginBox").classList.add("hidden");
  $("settingsBox").classList.remove("hidden");
  $("loggedInUser").textContent="Logged in: "+user;
  $("roleBadge").textContent=(role||"").toUpperCase();
  $("roleBadge").classList.toggle("operator",role==="operator");
  $("securityPanel").classList.toggle("hidden",role!=="admin");
  $("reportPanel").classList.toggle("hidden",role!=="admin");
  $("roleDescription").textContent=role==="admin"
    ? "Administrator access: setpoints, RUNNING / NOT IN USE, calibration offsets, schedules and account passwords."
    : "Operator access: alarm notification scheduler only. Setpoints, calibration, equipment mode and passwords are locked.";
}

async function logout(){await fetch("/api/admin/logout",{method:"POST"});location.reload();}

const dayNames=["Mon","Tue","Wed","Thu","Fri","Sat","Sun"];
function schedulerDays(d){
  const selected=new Set((d.schedule_days||[]).map(Number));
  return `<div class="schedule-days">${dayNames.map((name,idx)=>`<label class="day-chip day-switch"><input type="checkbox" class="schedule-day-${d.logger_sn}" value="${idx}" ${selected.has(idx)?"checked":""}><span><b>${name}</b><em class="day-state"></em></span></label>`).join("")}</div>`;
}
function setDaySelection(sn,days){const on=new Set(days.map(Number));document.querySelectorAll(".schedule-day-"+sn).forEach(el=>{el.checked=on.has(Number(el.value));});}
function applyWeekdays24hToCard(sn){$("schedule-enabled-"+sn).value="on";$("schedule-profile-"+sn).value="24h";$("schedule-start-"+sn).value="00:00";$("schedule-end-"+sn).value="00:00";setDaySelection(sn,[0,1,2,3,4]);updateScheduleProfile(sn);$("msg-"+sn).innerHTML='<span class="good">Mon–Fri 24H selected. Click Save Schedule.</span>';}
function updateScheduleProfile(sn){const profile=$("schedule-profile-"+sn)?.value||"custom";const times=$("schedule-times-"+sn);if(!times)return;const is24=profile==="24h";times.classList.toggle("schedule-times-disabled",is24);$("schedule-start-"+sn).disabled=is24;$("schedule-end-"+sn).disabled=is24;if(is24){$("schedule-start-"+sn).value="00:00";$("schedule-end-"+sn).value="00:00";}}
function signed(v,suffix){const n=Number(v||0);return `${n>=0?"+":""}${n.toFixed(1)} ${suffix}`;}

async function applyWeekday24hAll(){
  const msg=$("allScheduleMsg");
  if(!confirm("Apply notifications ON Monday-Friday 24 hours and OFF Saturday-Sunday to ALL loggers?"))return;
  msg.textContent="Applying schedule to all loggers…";
  const r=await fetch("/api/admin/schedule/all",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({schedule_enabled:true,schedule_days:[0,1,2,3,4],schedule_start:"00:00",schedule_end:"00:00"})});
  const x=await r.json().catch(()=>({}));
  if(!r.ok){msg.innerHTML='<span class="bad">'+(x.detail||"Unable to apply schedule")+'</span>';return;}
  msg.innerHTML='<span class="good">Applied to '+x.count+' loggers: Mon–Fri ON 24H · Sat–Sun OFF.</span>';
  await loadConfig();
}

function adminControls(d){
  if(currentRole!=="admin")return "";
  const monitorOnly=d.alarm_type==="monitor";
  const alarmFields=monitorOnly
    ? `<div class="monitor-only-note"><b>Temperature + Humidity Monitor</b><br>${d.model||"Logger"} is displayed and logged without a temperature alarm setpoint. Calibration offsets remain available below.</div>`
    : `<label>Temperature alarm setpoint (°C)<input id="limit-${d.logger_sn}" type="number" step="0.1" min="-40" max="100" value="${Number(d.limit).toFixed(1)}"></label>
       <label>Equipment operating mode<select id="mode-${d.logger_sn}"><option value="running" ${d.monitoring_enabled?"selected":""}>RUNNING — permit alarms</option><option value="idle" ${d.monitoring_enabled?"":"selected"}>NOT IN USE — suppress all alarms</option></select></label>`;
  return `<div class="admin-only-block"><div class="control-section-title">Admin Controls</div>${alarmFields}
    <div class="calibration-box"><div class="schedule-title">Reading Calibration Offset</div><p class="calibration-help">Adjusted reading = LI-COR raw reading + offset. Use a negative value when the logger reads high; positive when it reads low.</p>
      <div class="calibration-grid"><label>Temperature offset (°C)<input id="temp-offset-${d.logger_sn}" type="number" step="0.1" min="-20" max="20" value="${Number(d.temperature_offset||0).toFixed(1)}"></label>
      <label>Humidity offset (%RH)<input id="rh-offset-${d.logger_sn}" type="number" step="0.1" min="-50" max="50" value="${Number(d.humidity_offset||0).toFixed(1)}"></label></div>
      <div class="calibration-status">Current calibration: Temperature ${signed(d.temperature_offset,"°C")} · Humidity ${signed(d.humidity_offset,"%RH")}</div>
    </div></div>`;
}

function scheduleControls(d){
  if(d.alarm_type==="monitor") return `<div class="schedule-box monitor-only-schedule"><div class="schedule-title">Alarm Notification Scheduler</div><div class="schedule-current good">Not required for this monitor-only logger.</div></div>`;
  return `<div class="schedule-box"><div class="schedule-title">Alarm Notification Scheduler <span class="access-tag">Admin + Operator</span></div>
    <label>Weekly scheduler<select id="schedule-enabled-${d.logger_sn}"><option value="off" ${d.schedule_enabled?"":"selected"}>OFF — manual mode only</option><option value="on" ${d.schedule_enabled?"selected":""}>ON — follow selected days / hours</option></select></label>
    <label>Schedule type<select id="schedule-profile-${d.logger_sn}" onchange="updateScheduleProfile('${d.logger_sn}')"><option value="24h" ${d.schedule_start===d.schedule_end?"selected":""}>24 HOURS on selected ON days</option><option value="custom" ${d.schedule_start!==d.schedule_end?"selected":""}>Custom ON / OFF time each selected day</option></select></label>
    <button class="schedule-preset-btn" type="button" onclick="applyWeekdays24hToCard('${d.logger_sn}')">Preset: Mon–Fri 24H · Sat–Sun OFF</button>
    <div class="schedule-times ${d.schedule_start===d.schedule_end?"schedule-times-disabled":""}" id="schedule-times-${d.logger_sn}"><label>Alarm ON time<input id="schedule-start-${d.logger_sn}" type="time" value="${d.schedule_start||"00:00"}" ${d.schedule_start===d.schedule_end?"disabled":""}></label><label>Alarm OFF time<input id="schedule-end-${d.logger_sn}" type="time" value="${d.schedule_end||"00:00"}" ${d.schedule_start===d.schedule_end?"disabled":""}></label></div>
    <div class="schedule-label">Notification days · Pacific Time · ON = notifications enabled</div>${schedulerDays(d)}
    <div class="schedule-current ${d.schedule_active_now?"good":"warn"}">${d.schedule_enabled?`Scheduler is ${d.schedule_active_now?"ACTIVE":"OFF"} now · ${d.schedule_summary}`:"Scheduler OFF — manual operating mode controls alarms"}</div></div>`;
}

async function loadConfig(){
  const r=await fetch("/api/admin/config",{cache:"no-store"});
  if(r.status===401||r.status===403){location.reload();return;}
  const x=await r.json(); currentRole=x.role; currentUsername=x.username; showSettings(x.username,x.role);
  deviceMap=Object.fromEntries((x.devices||[]).map(d=>[String(d.logger_sn),d]));
  if($("operatorUsername"))$("operatorUsername").value=x.operator_username||"operator";
  if($("adminUsernameEdit"))$("adminUsernameEdit").value=x.username||"";
  if(x.report_config&&$("reportEnabled")){
    $("reportEnabled").value=x.report_config.enabled?"on":"off";
    $("reportTime").value=x.report_config.time||"08:00";
    $("reportRecipient").value=x.report_config.recipient||"";
    $("reportHours").value=String(x.report_config.hours||24);
  }
  const visibleDevices=currentRole==="operator" ? x.devices.filter(d=>d.alarm_type!=="monitor") : x.devices;
  $("configGrid").innerHTML=visibleDevices.map(d=>{const monitorOnly=d.alarm_type==="monitor";const headText=monitorOnly?`${d.model||"Logger"} · TEMPERATURE + HUMIDITY MONITOR`:`${d.alarm_type.toUpperCase()} alarm`;const badge=monitorOnly?"MONITOR":(d.effective_mode||"RUNNING");return `<article class="config-card ${(!monitorOnly&&!d.effective_monitoring_enabled)?"config-disabled":""}"><div class="config-head"><div><h3>${d.name}</h3><small>Logger ${d.logger_sn} · ${headText}</small></div><span class="pill ${(!monitorOnly&&!d.effective_monitoring_enabled)?"warn":""}">${badge}</span></div>
    ${adminControls(d)}${scheduleControls(d)}
    <div class="config-warning">${monitorOnly?"Temperature and humidity are displayed/logged. Admin can apply calibration offsets; no temperature alarm is configured.":(currentRole==="operator"?"Operator access: schedule changes only.":(!d.monitoring_enabled?"Temperature is still displayed/logged, but alarms are suppressed by NOT IN USE.":"Admin controls are enabled. Calibration offsets affect displayed/logged readings and temperature alarm evaluation."))}</div>
    <button class="primary" onclick="saveDevice('${d.logger_sn}')">${monitorOnly?"Save Calibration":(currentRole==="admin"?"Save All Settings":"Save Schedule")} — ${d.name}</button><div id="msg-${d.logger_sn}" class="admin-message"></div></article>`;}).join("");
  visibleDevices.filter(d=>d.alarm_type!=="monitor").forEach(d=>updateScheduleProfile(d.logger_sn));
}

async function saveDevice(sn){
  const d=deviceMap[String(sn)]||{};
  const monitorOnly=d.alarm_type==="monitor";
  const msg=$("msg-"+sn);
  const payload={};

  if(!monitorOnly){
    const schedule_enabled=$("schedule-enabled-"+sn).value==="on";
    const profile=$("schedule-profile-"+sn).value;
    const schedule_start=profile==="24h"?"00:00":$("schedule-start-"+sn).value;
    const schedule_end=profile==="24h"?"00:00":$("schedule-end-"+sn).value;
    const schedule_days=[...document.querySelectorAll(".schedule-day-"+sn+":checked")].map(el=>Number(el.value));
    if(schedule_enabled&&!schedule_days.length){msg.innerHTML='<span class="bad">Select at least one active day.</span>';return;}
    Object.assign(payload,{schedule_enabled,schedule_start,schedule_end,schedule_days});
  }

  if(currentRole==="admin"){
    if(!monitorOnly){payload.limit=Number($("limit-"+sn).value);payload.monitoring_enabled=$("mode-"+sn).value==="running";}
    payload.temperature_offset=Number($("temp-offset-"+sn).value);
    payload.humidity_offset=Number($("rh-offset-"+sn).value);
  }
  msg.textContent="Saving…";
  const r=await fetch("/api/admin/config/"+sn,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
  const x=await r.json().catch(()=>({}));
  if(!r.ok){msg.innerHTML='<span class="bad">'+(x.detail||"Save failed")+'</span>';return;}
  msg.innerHTML='<span class="good">Saved and recorded in Event Log.</span>';setTimeout(loadConfig,700);
}

async function changePassword(){
  const msg=$("passwordMsg");
  const new_username=($("adminUsernameEdit")?.value||"").trim();
  const current_password=$("currentPassword").value;
  const new_password=$("newPassword").value;
  const confirm_password=$("confirmPassword").value;
  if(!new_username){msg.innerHTML='<span class="bad">Enter an Admin username.</span>';return;}
  if(new_username.length>64){msg.innerHTML='<span class="bad">Admin username must be 64 characters or fewer.</span>';return;}
  if(/[\r\n]/.test(new_username)){msg.innerHTML='<span class="bad">Admin username cannot contain line breaks.</span>';return;}
  if(!current_password||!new_password||!confirm_password){msg.innerHTML='<span class="bad">Complete all Admin password fields.</span>';return;}
  if(!/^[A-Za-z0-9]{6}$/.test(new_password)){msg.innerHTML='<span class="bad">New Admin password must be exactly 6 letters or numbers.</span>';return;}
  if(new_password!==confirm_password){msg.innerHTML='<span class="bad">New Admin passwords do not match.</span>';return;}
  const r=await fetch("/api/admin/change-password",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({new_username,current_password,new_password,confirm_password})});
  const x=await r.json().catch(()=>({}));
  if(!r.ok){msg.innerHTML='<span class="bad">'+(x.detail||"Admin login change failed")+'</span>';return;}
  $("currentPassword").value=$("newPassword").value=$("confirmPassword").value="";
  if($("adminUsernameEdit"))$("adminUsernameEdit").value=x.username||new_username;
  msg.innerHTML='<span class="good">Admin username/password updated successfully.</span>';
}

async function setOperatorPassword(){
  const msg=$("operatorPasswordMsg"),username=$("operatorUsername").value.trim()||"operator",new_password=$("operatorPassword").value,confirm_password=$("operatorConfirm").value;
  if(!username){msg.innerHTML='<span class="bad">Enter an Operator username.</span>';return;}
  if(username.length>64||/[\\r\\n]/.test(username)){msg.innerHTML='<span class="bad">Operator username must be 1-64 printable characters without line breaks.</span>';return;}
  if(!new_password||!confirm_password){msg.innerHTML='<span class="bad">Enter and confirm the Operator password.</span>';return;}
  if(!/^[A-Za-z0-9]{6}$/.test(new_password)){msg.innerHTML='<span class="bad">Operator password must be exactly 6 letters or numbers.</span>';return;}
  if(new_password!==confirm_password){msg.innerHTML='<span class="bad">Operator passwords do not match.</span>';return;}
  const r=await fetch("/api/admin/operator-password",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username,new_password,confirm_password})});
  const x=await r.json().catch(()=>({}));if(!r.ok){msg.innerHTML='<span class="bad">'+(x.detail||"Unable to save Operator login")+'</span>';return;}
  $("operatorPassword").value=$("operatorConfirm").value="";msg.innerHTML='<span class="good">Operator login saved. Username: '+x.username+'</span>';
}


async function saveReportConfig(){
  const msg=$("reportConfigMsg");
  const payload={
    enabled:$("reportEnabled").value==="on",
    time:$("reportTime").value||"08:00",
    recipient:$("reportRecipient").value.trim(),
    hours:Number($("reportHours").value||24)
  };
  msg.textContent="Saving…";
  const r=await fetch("/api/admin/report-config",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
  const x=await r.json().catch(()=>({}));
  if(!r.ok){msg.innerHTML='<span class="bad">'+(x.detail||"Unable to save report settings")+'</span>';return;}
  msg.innerHTML='<span class="good">Daily report saved. Next automatic email follows the Pacific Time schedule.</span>';
}

async function sendReportNow(){
  const msg=$("reportSendMsg");
  msg.textContent="Building 24-hour report and sending email…";
  const r=await fetch("/api/admin/send-maintenance-report",{method:"POST"});
  const x=await r.json().catch(()=>({}));
  if(!r.ok){msg.innerHTML='<span class="bad">'+(x.detail||"Report email failed")+'</span>';return;}
  msg.innerHTML='<span class="good">Report emailed to '+x.recipient+'.</span>';
}

status();
