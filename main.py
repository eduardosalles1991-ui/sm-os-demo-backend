@app.get("/widget", response_class=HTMLResponse)
def widget():
    return HTMLResponse(
        """<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>S&M OS 6.1 — Demo</title>
  <style>
    body{margin:0;font-family:system-ui,Segoe UI,Arial;background:#07080c;color:#eef1f7}
    .wrap{max-width:900px;margin:0 auto;padding:18px}
    .box{background:#0f111a;border:1px solid rgba(245,196,81,.25);border-radius:18px;overflow:hidden}
    .head{padding:16px 18px;background:linear-gradient(135deg,#0b0d12,#171b24);border-bottom:3px solid #f5c451}
    .title{font-weight:800;letter-spacing:.3px}
    .sub{opacity:.8;font-size:13px;margin-top:4px}
    #chatLog{height:420px;overflow:auto;padding:16px;background:#07080c}
    .row{display:flex;gap:10px;padding:14px;background:#0b0d12;border-top:1px solid rgba(255,255,255,.08)}
    input{flex:1;padding:12px;border-radius:12px;border:1px solid rgba(255,255,255,.12);background:#0f111a;color:#eef1f7;outline:none}
    button{padding:12px 14px;border-radius:12px;border:1px solid rgba(245,196,81,.35);
      background:linear-gradient(180deg,#f5c451,#c9921c);font-weight:900;cursor:pointer;color:#1a1204}
    .pill{display:flex;gap:10px;padding:14px;background:#0b0d12;border-top:1px solid rgba(255,255,255,.08);align-items:center}
    .badge{font-size:12px;color:rgba(245,196,81,.95);border:1px solid rgba(245,196,81,.25);padding:6px 10px;border-radius:999px;background:rgba(245,196,81,.06)}
    .btn2{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);color:#eef1f7;font-weight:800}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="box">
      <div class="head">
        <div class="title">S&M OS 6.1 — Diagnóstico Jurídico Inteligente (DEMO)</div>
        <div class="sub">Chat guiado • Relatório estruturado • Sem expor o módulo interno</div>
      </div>

      <div class="pill">
        <span class="badge">Ativação</span>
        <input id="keyInput" placeholder="Cole aqui o DEMO_KEY" />
        <button id="keyBtn">Ativar</button>
        <button id="resetBtn" class="btn2" style="margin-left:auto;">Reiniciar</button>
      </div>

      <div id="chatLog"></div>

      <div class="row">
        <input id="chatInput" placeholder="Digite aqui..." disabled />
        <button id="chatSend" disabled>Enviar</button>
      </div>
    </div>
  </div>

<script>
  const STORE="sm_os_demo_key";
  let sessionId=null;
  let DEMO_KEY=localStorage.getItem(STORE)||"";

  const log=document.getElementById("chatLog");
  const input=document.getElementById("chatInput");
  const btn=document.getElementById("chatSend");
  const keyInput=document.getElementById("keyInput");
  const keyBtn=document.getElementById("keyBtn");
  const resetBtn=document.getElementById("resetBtn");

  keyInput.value=DEMO_KEY;

  function addMsg(role,text){
    const wrap=document.createElement("div");
    wrap.style.marginBottom="12px";
    wrap.style.display="flex";
    wrap.style.justifyContent=role==="user"?"flex-end":"flex-start";

    const bubble=document.createElement("div");
    bubble.style.maxWidth="78%";
    bubble.style.padding="12px";
    bubble.style.borderRadius="14px";
    bubble.style.whiteSpace="pre-wrap";
    bubble.style.lineHeight="1.45";
    bubble.style.fontSize="14px";
    bubble.style.background=role==="user"?"rgba(245,196,81,.16)":"rgba(255,255,255,.06)";
    bubble.style.border=role==="user"?"1px solid rgba(245,196,81,.22)":"1px solid rgba(255,255,255,.10)";
    bubble.textContent=text;

    wrap.appendChild(bubble);
    log.appendChild(wrap);
    log.scrollTop=log.scrollHeight;
  }

  async function fetchWithTimeout(url, options={}, ms=60000){
    const controller = new AbortController();
    const id = setTimeout(() => controller.abort(), ms);
    try{
      const res = await fetch(url, { ...options, signal: controller.signal });
      clearTimeout(id);
      return res;
    } catch (e){
      clearTimeout(id);
      throw e;
    }
  }

  function setBusy(isBusy){
    input.disabled = isBusy || !sessionId;
    btn.disabled = isBusy || !sessionId;
    keyBtn.disabled = isBusy;
    resetBtn.disabled = isBusy;
  }

  async function startSession(){
    if(!DEMO_KEY){
      addMsg("assistant","Cole o DEMO_KEY e clique em Ativar.");
      return;
    }
    setBusy(true);
    addMsg("assistant","⏳ Iniciando sessão…");
    try{
      const res=await fetchWithTimeout("/session/new",{method:"POST",headers:{"x-demo-key":DEMO_KEY}}, 60000);
      const data=await res.json();
      if(!res.ok){
        addMsg("assistant","Erro ao iniciar: "+(data.detail||res.status));
        sessionId=null;
        setBusy(false);
        return;
      }
      sessionId=data.session_id;
      addMsg("assistant",data.message);
      setBusy(false);
      input.focus();
    }catch(e){
      addMsg("assistant","⚠️ Falha de rede/timeout ao iniciar. Se o Render estava dormindo, tente novamente em 10s.");
      sessionId=null;
      setBusy(false);
    }
  }

  async function send(){
    const text=input.value.trim();
    if(!text) return;
    input.value="";
    addMsg("user",text);

    setBusy(true);
    addMsg("assistant","⏳ Processando…");
    try{
      const res=await fetchWithTimeout("/chat",{
        method:"POST",
        headers:{"Content-Type":"application/json","x-demo-key":DEMO_KEY},
        body:JSON.stringify({session_id:sessionId,message:text})
      }, 60000);

      const data=await res.json().catch(()=> ({}));

      if(!res.ok){
        addMsg("assistant","Erro: "+(data.detail||res.status));
        setBusy(false);
        return;
      }

      // sessão inválida (Render reiniciou) → orientar reinício
      if((data.message||"").includes("Sessão inválida")){
        addMsg("assistant","⚠️ A sessão expirou (provável reinício do servidor). Clique em Reiniciar e continue.");
        sessionId=null;
        setBusy(false);
        return;
      }

      addMsg("assistant",data.message||"(sem mensagem)");
      if(data.report){
        addMsg("assistant","✅ RELATÓRIO GERADO:\\n\\n"+data.report);
      }
      setBusy(false);
    }catch(e){
      addMsg("assistant","⚠️ Falha de rede/timeout. Se o Render dormiu, aguarde 10–20s e clique em Reiniciar.");
      setBusy(false);
    }
  }

  keyBtn.addEventListener("click",()=>{
    DEMO_KEY=keyInput.value.trim();
    localStorage.setItem(STORE,DEMO_KEY);
    addMsg("assistant","Código registrado.");
    startSession();
  });

  resetBtn.addEventListener("click",()=>{
    sessionId=null;
    addMsg("assistant","🔄 Reiniciando diagnóstico…");
    startSession();
  });

  btn.addEventListener("click",send);
  input.addEventListener("keydown",(e)=>{ if(e.key==="Enter") send(); });

  addMsg("assistant", DEMO_KEY ? "Código encontrado. Clique em Ativar." : "Cole o DEMO_KEY e clique em Ativar.");
</script>
</body>
</html>
"""
    )
