
const log=document.getElementById("alarmLog");
function f(v){return v==null?"—":Number(v).toFixed(1)+" °C"}
async function loadEvents(){try{let r=await fetch("/api/alarm-events?limit=500"),x=await r.json(),rows=x.events||[];log.innerHTML=rows.length?rows.slice().reverse().map(e=>`<tr><td>${new Date(e.time).toLocaleString()}</td><td>${e.name||e.logger_sn}</td><td><b>${e.event}</b>${e.detail?`<br><small>${e.detail}</small>`:""}</td><td>${f(e.reading)}</td><td>${e.limit==null?"—":(e.alarm_type==="low"?"Low ":"High ")+Number(e.limit).toFixed(1)+" °C"}</td><td>${e.email||"—"}</td><td>${e.push||"—"}</td></tr>`).join(""):`<tr><td colspan="7">No server alarm events recorded yet.</td></tr>`}catch(e){log.innerHTML=`<tr><td colspan="7">Unable to load event history.</td></tr>`}}
loadEvents();setInterval(loadEvents,30000);
