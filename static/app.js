const unitBtn=document.getElementById("unitBtn");
const conn=document.getElementById("conn");
const alarmBanner=document.getElementById("alarmBanner");
const alarmText=document.getElementById("alarmText");
const normal=document.getElementById("normal");
const alarms=document.getElementById("alarms");
const online=document.getElementById("online");
const offline=document.getElementById("offline");
const cards=document.getElementById("cards");
const status=document.getElementById("status");
const alarmLog=document.getElementById("alarmLog");


async function testAlarmEmail(ev){
 const b=ev?.target;if(b){b.disabled=true;b.textContent="Sending Alarm Test..."}
 try{let r=await fetch("/api/test-alarm-email",{method:"POST"}),x=await r.json();if(!r.ok)throw new Error(x.detail||"Failed");alert(x.message)}
 catch(e){alert("Alarm email test failed: "+e.message)}
 finally{if(b){b.disabled=false;b.textContent="Test T3 Alarm Email"}}
}
async function emailStatus(){
 try{let r=await fetch("/api/email-status"),x=await r.json();alert(
 `Gmail configured: ${x.configured?"YES":"NO"}\nSender: ${x.sender||"(missing)"}\nRecipient: ${x.recipient||"(missing)"}\nLast sent: ${x.last_success||"None"}\nLast subject: ${x.last_subject||"None"}\nLast error: ${x.last_error||"None"}`
 )}catch(e){alert("Could not read email status: "+e.message)}
}
async function testEmail(ev){
 const btn=ev?.target;if(btn){btn.disabled=true;btn.textContent="Sending..."}
 try{let r=await fetch("/api/test-email",{method:"POST"}),x=await r.json();if(!r.ok)throw new Error(x.detail||"Email test failed");alert(x.message||"Test email sent.")}
 catch(e){alert("Test email failed: "+e.message+"\n\nCheck Gmail settings in .env and restart the dashboard.")}
 finally{if(btn){btn.disabled=false;btn.textContent="Test Email"}}
}
let selected=null,useF=false;
const cv=c=>useF?(c*9/5+32):c;
const unit=()=>useF?"°F":"°C";
const f=v=>v==null?"--":cv(Number(v)).toFixed(1);
const tm=s=>s?new Date(s).toLocaleString():"No recent data";
function toggleUnit(){useF=!useF;renderLast();loadAllTrends(window.lastMinutes||60)}
function toggleFull(){if(!document.fullscreenElement)document.documentElement.requestFullscreen();else document.exitFullscreen()}
async function acknowledge(){
 if(!lastData)return;
 const active=lastData.devices.filter(d=>d.alarm&&!d.acked);
 await Promise.all(active.map(d=>fetch(`/api/ack/${d.logger_sn}`,{method:"POST"})));
 await load(); await loadAlarmLog();
}
async function clearLog(){
 if(!confirm("Clear the persistent alarm event log?"))return;
 await fetch("/api/alarm-events",{method:"DELETE"}); await loadAlarmLog();
}
async function loadAlarmLog(){
 try{
  const r=await fetch("/api/alarm-events?limit=250"); const x=await r.json(); const rows=x.events||[];
  alarmLog.innerHTML=rows.length?rows.slice().reverse().map(e=>{
   const reading=e.reading==null?"—":f(e.reading)+" "+unit();
   const limit=e.limit==null?"—":(e.alarm_type==="low"?"Low ":"High ")+f(e.limit)+" "+unit();
   const when=new Date(e.time).toLocaleString();
   return `<tr><td>${when}</td><td>${e.name||e.logger_sn}</td><td><b>${e.event}</b>${e.detail?`<br><small>${e.detail}</small>`:""}</td><td>${reading}</td><td>${limit}</td><td>${e.email||"—"}</td><td>${e.push||"—"}</td></tr>`;
  }).join(""):`<tr><td colspan="7">No server alarm events recorded yet.</td></tr>`;
 }catch(e){alarmLog.innerHTML=`<tr><td colspan="7">Unable to load server alarm log: ${e.message}</td></tr>`}
}
let lastData=null;
function renderLast(){if(lastData)render(lastData)}
async function load(){try{conn.textContent="Refreshing…";let r=await fetch("/api/current");if(!r.ok)throw new Error(await r.text());let x=await r.json();lastData=x;render(x)}catch(e){conn.innerHTML='<span class="bad">● CONNECTION ERROR</span>'}}
function render(x){
 let a=0,on=0,off=0,c=document.getElementById("cards");c.innerHTML="";let active=[];
 x.devices.forEach(d=>{
   if(d.alarm){a++;active.push(d)} if(d.comm_status==="ONLINE")on++;else off++;
   let e=document.createElement("div");e.className="card "+(d.alarm?"alarm ":(d.comm_status!=="ONLINE"?"stale ":""));
   let label=d.alarm_type=="low"?"LOW TEMPERATURE":"HIGH TEMPERATURE";
   let state=d.comm_status!=="ONLINE"?`● ${d.comm_status}`:d.alarm?"● "+label:"● NORMAL";
   e.innerHTML=`<h3>${d.name}</h3><div class="reading">${f(d.temperature)} ${unit()}</div><div class="rh">${d.rh==null?"--":Number(d.rh).toFixed(1)} % RH</div><div class="${d.alarm?"bad":d.comm_status!=="ONLINE"?"muted":"good"}">${state}</div><div class="meta">${d.alarm_type=="low"?"Low":"High"} limit: ${f(d.limit)} ${unit()}<br>Logger: ${d.logger_sn}<br>Latest: ${tm(d.timestamp)}<br>Age: ${d.age_minutes==null?"--":d.age_minutes+" min"}${d.acked?"<br><b>Alarm: ACKNOWLEDGED</b>":""}</div>`;
   e.onclick=()=>{document.getElementById("trend-"+d.logger_sn)?.scrollIntoView({behavior:"smooth",block:"center"})};c.appendChild(e)
 });
 normal.textContent=x.devices.filter(d=>!d.alarm&&d.comm_status==="ONLINE").length;alarms.textContent=a;online.textContent=on;offline.textContent=off;
 status.innerHTML=x.devices.map(d=>{
   const cls=d.comm_status==="ONLINE"?"good":d.comm_status==="STALE"?"warn":"bad";
   return `<div class="comm-row"><div><b>${d.name}</b><small>Logger ${d.logger_sn}</small></div><div><span class="status-pill ${cls}">● ${d.comm_status}</span><small>Last: ${tm(d.timestamp)} · ${d.age_minutes==null?"No data":d.age_minutes+" min old"}</small></div></div>`
 }).join("");
 if(active.length){
   alarmBanner.classList.remove("hidden"); const allAcked=active.every(d=>d.acked);
   if(allAcked){alarmBanner.classList.add("acked");alarmBanner.querySelector("strong").textContent="ALARM ACKNOWLEDGED — CONDITION STILL ACTIVE"}
   else{alarmBanner.classList.remove("acked");alarmBanner.querySelector("strong").textContent="ACTIVE TEMPERATURE ALARM"}
   alarmText.innerHTML=active.map(d=>`${d.name}: ${f(d.temperature)} ${unit()} (${d.alarm_type=="low"?"LOW":"HIGH"} limit ${f(d.limit)} ${unit()})${d.acked?" — ACKNOWLEDGED":""}`).join(" &nbsp; | &nbsp; ");
 }else{alarmBanner.classList.add("hidden");alarmBanner.classList.remove("acked");alarmBanner.querySelector("strong").textContent="ACTIVE TEMPERATURE ALARM"}
 conn.innerHTML='<span class="good">● LI-COR CONNECTED</span><br><small>'+new Date().toLocaleTimeString()+'</small>';
 loadAlarmLog(); loadAllTrends(window.lastMinutes||60)
}
async function hist(m){window.lastMinutes=m;if(!selected)return;let r=await fetch(`/api/history/${selected.logger_sn}?minutes=${m}`);if(!r.ok)return;let x=await r.json();let pts=x.data.filter(q=>(q.sensor_measurement_type||"").toLowerCase()=="temperature").map(q=>({t:new Date(q.timestamp),v:cv(Number(q.value))}));draw(pts,cv(selected.limit),selected.alarm_type)}
function draw(pts,lim,typ){let c=chart,ctx=c.getContext("2d"),w=c.clientWidth,h=300,d=devicePixelRatio||1;c.width=w*d;c.height=h*d;ctx.scale(d,d);ctx.clearRect(0,0,w,h);ctx.font="12px Segoe UI";if(!pts.length){ctx.fillText("No temperature history returned.",20,40);return}let vals=pts.map(p=>p.v),mn=Math.min(...vals,lim)-1,mx=Math.max(...vals,lim)+1,p={l:52,r:25,t:20,b:38},cw=w-p.l-p.r,ch=h-p.t-p.b;ctx.strokeStyle="#dce4ea";ctx.fillStyle="#687885";for(let i=0;i<5;i++){let v=mn+(mx-mn)*i/4,y=p.t+ch-i/4*ch;ctx.beginPath();ctx.moveTo(p.l,y);ctx.lineTo(w-p.r,y);ctx.stroke();ctx.fillText(v.toFixed(1)+"°",7,y+4)}ctx.strokeStyle="#177fb5";ctx.lineWidth=3;ctx.beginPath();pts.forEach((q,i)=>{let x=p.l+i/Math.max(pts.length-1,1)*cw,y=p.t+ch-(q.v-mn)/(mx-mn)*ch;i?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.stroke();let y=p.t+ch-(lim-mn)/(mx-mn)*ch;ctx.setLineDash([7,5]);ctx.strokeStyle="#c83f49";ctx.beginPath();ctx.moveTo(p.l,y);ctx.lineTo(w-p.r,y);ctx.stroke();ctx.setLineDash([]);ctx.fillStyle="#a8323b";ctx.fillText((typ=="low"?"Low ":"High ")+lim.toFixed(1)+unit(),Math.max(p.l,w-130),y-5)}

async function loadAllTrends(m){
 window.lastMinutes=m;
 if(!lastData)return;
 const grid=document.getElementById("trendGrid"); if(!grid)return;
 grid.innerHTML=lastData.devices.map(d=>`<div id="trend-${d.logger_sn}" class="trend-card ${d.alarm?"alarm":"normal"}"><h3>${d.name}</h3><small>${d.alarm_type=="low"?"Low":"High"} temp limit ${f(d.limit)} ${unit()} · Current ${f(d.temperature)} ${unit()} · RH ${f(d.rh)}%</small><div class="mini-label">Temperature</div><canvas id="temp-${d.logger_sn}"></canvas><div class="mini-label">Relative Humidity</div><canvas id="rh-${d.logger_sn}"></canvas><button class="download-btn" onclick="download24h('${d.logger_sn}')">Download 24h Temp + RH CSV</button></div>`).join("");
 await Promise.all(lastData.devices.map(async d=>{
   try{
    let r=await fetch(`/api/history/${d.logger_sn}?minutes=${m}`); if(!r.ok)return;
    let x=await r.json();
    let tp=x.data.filter(q=>(q.sensor_measurement_type||"").toLowerCase()=="temperature").map(q=>({t:new Date(q.timestamp),v:cv(Number(q.value))}));
    let rh=x.data.filter(q=>(q.sensor_measurement_type||"").toLowerCase()=="rh").map(q=>({t:new Date(q.timestamp),v:Number(q.value)}));
    drawSeries(document.getElementById("temp-"+d.logger_sn),tp,cv(d.limit),d.alarm_type,"temp");
    drawSeries(document.getElementById("rh-"+d.logger_sn),rh,null,null,"rh");
   }catch(e){}
 }));
}
function drawSeries(c,pts,lim,typ,kind){
 if(!c)return;let ctx=c.getContext("2d"),w=c.clientWidth,h=180,d=devicePixelRatio||1;c.width=w*d;c.height=h*d;ctx.scale(d,d);ctx.clearRect(0,0,w,h);ctx.font="11px Segoe UI";
 if(!pts.length){ctx.fillText("No history returned.",15,35);return}
 let vals=pts.map(p=>p.v),mn,mx;
 if(kind==="rh"){mn=Math.max(0,Math.floor(Math.min(...vals)-5));mx=Math.min(100,Math.ceil(Math.max(...vals)+5));if(mx<=mn){mn=0;mx=100}}
 else{mn=Math.min(...vals,lim)-1;mx=Math.max(...vals,lim)+1}
 let p={l:45,r:18,t:15,b:25},cw=w-p.l-p.r,ch=h-p.t-p.b;
 ctx.strokeStyle="#e0e6eb";ctx.fillStyle="#687885";
 for(let i=0;i<4;i++){let v=mn+(mx-mn)*i/3,y=p.t+ch-i/3*ch;ctx.beginPath();ctx.moveTo(p.l,y);ctx.lineTo(w-p.r,y);ctx.stroke();ctx.fillText(v.toFixed(1)+(kind==="rh"?"%":"°"),4,y+4)}
 ctx.strokeStyle=kind==="rh"?"#6f42c1":"#177fb5";ctx.lineWidth=2.3;ctx.beginPath();pts.forEach((q,i)=>{let x=p.l+i/Math.max(pts.length-1,1)*cw,y=p.t+ch-(q.v-mn)/(mx-mn)*ch;i?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.stroke();
 if(kind==="temp"&&lim!=null){let y=p.t+ch-(lim-mn)/(mx-mn)*ch;ctx.setLineDash([6,4]);ctx.strokeStyle="#c83f49";ctx.beginPath();ctx.moveTo(p.l,y);ctx.lineTo(w-p.r,y);ctx.stroke();ctx.setLineDash([]);ctx.fillStyle="#a8323b";ctx.fillText((typ=="low"?"Low ":"High ")+lim.toFixed(1)+unit(),Math.max(p.l,w-105),y-4)}
}
function download24h(sn){window.location.href=`/api/download-24h/${sn}`}
function downloadAll24h(){
 if(!lastData)return;
 lastData.devices.forEach((d,i)=>setTimeout(()=>download24h(d.logger_sn),i*450));
}
loadAlarmLog();load();setInterval(load,60000);setInterval(loadAlarmLog,30000);
async function testPhonePush(ev){
  if(ev) ev.preventDefault();
  try{
    const r=await fetch('/api/test-push',{method:'POST'});
    const text=await r.text();
    let d;
    try { d=JSON.parse(text); }
    catch { throw new Error('Server returned HTTP '+r.status+': '+text.slice(0,160)); }
    if(d.ok) alert('Test phone push sent. Check your iPhone.');
    else alert('Phone push error: '+(d.error||'Unknown error'));
  }catch(e){ alert('Phone push test error: '+e.message); }
}
async function showPushStatus(ev){
  if(ev) ev.preventDefault();
  try{
    const r=await fetch('/api/push-status');
    const d=await r.json();
    alert('Phone Push: '+(d.configured?'CONFIGURED':'NOT CONFIGURED')+
          '\nService: ntfy'+
          '\nLast success: '+(d.last_success||'None')+
          '\nLast error: '+(d.last_error||'None'));
  }catch(e){ alert('Push status error: '+e); }
}
