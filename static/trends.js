
let minutes=1440,useF=false,lastData=null;
const $=id=>document.getElementById(id),cv=c=>useF?(c*9/5+32):c,unit=()=>useF?"°F":"°C";
function rangeLabel(){
  if(minutes===60)return "1 Hour";
  if(minutes===1440)return "24 Hours";
  if(minutes===10080)return "7 Days";
  if(minutes%1440===0)return `${minutes/1440} Days`;
  if(minutes%60===0)return `${minutes/60} Hours`;
  return `${minutes} Minutes`;
}
function updateDownloadLabels(){
  const label=rangeLabel();
  const all=$("downloadAll");
  if(all)all.textContent=`Download ${label} - All Loggers Temp + RH CSV`;
  document.querySelectorAll(".logger-download").forEach(b=>{
    b.textContent=`Download ${label} Temp + RH CSV`;
  });
}
function downloadAllLoggers(){
  location.href=`/api/download-range-all.csv?minutes=${minutes}`;
}
function localTodayISO(){
  const d=new Date();
  const y=d.getFullYear();
  const m=String(d.getMonth()+1).padStart(2,"0");
  const day=String(d.getDate()).padStart(2,"0");
  return `${y}-${m}-${day}`;
}
function initializeHistoryCalendar(){
  const input=$("historyDate");
  if(input){
    const today=localTodayISO();
    input.max=today;
    if(!input.value)input.value=today;
  }
}
function populateHistoryLogger(){
  const sel=$("historyLogger");
  if(!sel||!lastData||!Array.isArray(lastData.devices))return;
  const current=sel.value||"ALL";
  sel.innerHTML='<option value="ALL">All Loggers</option>'+
    lastData.devices.map(d=>`<option value="${d.logger_sn}">${d.name} — ${d.logger_sn}</option>`).join("");
  if([...sel.options].some(o=>o.value===current))sel.value=current;
}
function downloadCalendarHistory(){
  const date=$("historyDate")?.value||"";
  const sn=$("historyLogger")?.value||"ALL";
  const msg=$("historyMessage");
  if(!date){
    if(msg)msg.innerHTML='<span class="bad">Select a calendar date first.</span>';
    return;
  }
  if(date>localTodayISO()){
    if(msg)msg.innerHTML='<span class="bad">Future dates are not available.</span>';
    return;
  }
  if(msg)msg.innerHTML='<span class="good">Preparing '+date+' history CSV…</span>';
  const url=sn==="ALL"
    ? `/api/download-date-all.csv?date=${encodeURIComponent(date)}`
    : `/api/download-date/${encodeURIComponent(sn)}?date=${encodeURIComponent(date)}`;
  window.location.href=url;
}
function toggleUnit(){useF=!useF;load()}
document.querySelectorAll("[data-min]").forEach(b=>b.onclick=()=>{minutes=Number(b.dataset.min);document.querySelectorAll("[data-min]").forEach(x=>x.classList.toggle("primary",x===b));updateDownloadLabels();load()});
async function load(){try{let r=await fetch("/api/current");lastData=await r.json();$("conn").innerHTML='<span class="good">● LI-COR CONNECTED</span>';populateHistoryLogger();renderCards();await Promise.all(lastData.devices.map(loadOne))}catch(e){$("conn").innerHTML='<span class="bad">● CONNECTION ERROR</span>'}}
function safeNum(v,digits=1){const n=Number(v);return Number.isFinite(n)?n.toFixed(digits):"--"}
function renderCards(){
  $("trendGrid").innerHTML=lastData.devices.map(d=>{
    const monitorOnly=d.alarm_type==="monitor";
    const temp=Number(d.temperature), rh=Number(d.rh);
    const tempText=Number.isFinite(temp)?cv(temp).toFixed(1):"--";
    const rhText=Number.isFinite(rh)?rh.toFixed(1):"--";
    const meta=monitorOnly
      ? `Current ${tempText} ${unit()} · RH ${rhText}% · ${d.model||"Monitor only"} · Monitor only`
      : `Current ${tempText} ${unit()} · RH ${rhText}% · ${d.alarm_type==="low"?"Low":"High"} limit ${cv(Number(d.limit)).toFixed(1)} ${unit()}`;
    return `<article class="trend-card"><h3>${d.name}</h3><div class="trend-meta">Logger ${d.logger_sn}${d.model?" · "+d.model:""}<br>${meta}</div><div class="chart-title">Temperature</div><canvas id="t-${d.logger_sn}"></canvas><div class="chart-title">Relative Humidity</div><canvas id="r-${d.logger_sn}"></canvas><button class="download-btn logger-download" onclick="location.href=\'/api/download-range/${d.logger_sn}?minutes=${minutes}\'">Download ${rangeLabel()} Temp + RH CSV</button></article>`;
  }).join("");
}
async function loadOne(d){
  let r=await fetch(`/api/history/${d.logger_sn}?minutes=${minutes}`);if(!r.ok)return;
  let x=await r.json();
  let t=x.data.filter(q=>(q.sensor_measurement_type||"").toLowerCase()==="temperature").map(q=>({t:new Date(q.timestamp),v:cv(Number(q.value))}));
  let h=x.data.filter(q=>(q.sensor_measurement_type||"").toLowerCase()==="rh").map(q=>({t:new Date(q.timestamp),v:Number(q.value)}));
  const limit=(d.alarm_type==="high"||d.alarm_type==="low")?cv(Number(d.limit)):null;
  draw($("t-"+d.logger_sn),t,limit,"temp");draw($("r-"+d.logger_sn),h,null,"rh");
}
function draw(c,pts,limit,kind){
  if(!c||!pts.length)return;
  let ctx=c.getContext("2d"),w=c.clientWidth,h=210,dp=devicePixelRatio||1;c.width=w*dp;c.height=h*dp;ctx.scale(dp,dp);ctx.clearRect(0,0,w,h);
  let vals=pts.map(p=>p.v).filter(Number.isFinite);if(!vals.length)return;
  let mn,mx;
  if(kind==="rh"){mn=Math.max(0,Math.min(...vals)-5);mx=Math.min(100,Math.max(...vals)+5);}
  else if(limit!=null&&Number.isFinite(limit)){mn=Math.min(...vals,limit)-2;mx=Math.max(...vals,limit)+2;}
  else{mn=Math.min(...vals)-2;mx=Math.max(...vals)+2;}
  if(mx<=mn)mx=mn+10;
  let p={l:48,r:15,t:15,b:28},cw=w-p.l-p.r,ch=h-p.t-p.b;ctx.font="11px Segoe UI";ctx.fillStyle="#8fb2c9";ctx.strokeStyle="#27475d";
  for(let i=0;i<4;i++){let v=mn+(mx-mn)*i/3,y=p.t+ch-i/3*ch;ctx.beginPath();ctx.moveTo(p.l,y);ctx.lineTo(w-p.r,y);ctx.stroke();ctx.fillText(v.toFixed(1)+(kind==="rh"?"%":"°"),4,y+4)}
  ctx.strokeStyle=kind==="rh"?"#8f7bff":"#3ab5ff";ctx.lineWidth=2.7;ctx.beginPath();pts.forEach((q,i)=>{let xx=p.l+i/Math.max(1,pts.length-1)*cw,yy=p.t+ch-(q.v-mn)/(mx-mn)*ch;i?ctx.lineTo(xx,yy):ctx.moveTo(xx,yy)});ctx.stroke();
  if(kind==="temp"&&limit!=null&&Number.isFinite(limit)){let y=p.t+ch-(limit-mn)/(mx-mn)*ch;ctx.setLineDash([6,4]);ctx.strokeStyle="#ff5d67";ctx.beginPath();ctx.moveTo(p.l,y);ctx.lineTo(w-p.r,y);ctx.stroke();ctx.setLineDash([])}
}
initializeHistoryCalendar();
updateDownloadLabels();
load();
