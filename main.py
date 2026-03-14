<div id="smos-app">
  <!-- Video BG -->
  <div class="smos-video-bg" aria-hidden="true">
    <iframe
      id="smos-yt"
      src="https://www.youtube.com/embed/s9xk77X4m5c?autoplay=1&mute=1&controls=0&rel=0&playsinline=1&loop=1&playlist=s9xk77X4m5c&modestbranding=1&iv_load_policy=3"
      frameborder="0"
      allow="autoplay; encrypted-media"
      allowfullscreen
      tabindex="-1"
    ></iframe>
  </div>
  <div class="smos-video-overlay" aria-hidden="true"></div>

  <!-- Shell -->
  <div class="smos-shell">
    <header class="smos-topbar">
      <div class="smos-brand">
        <div class="smos-logo">S&amp;M</div>
        <div class="smos-title">
          <div class="smos-h1">Diagnóstico Jurídico Inteligente</div>
          <div class="smos-h2">S&amp;M OS 6.1 • Demo • PDF/DOCX/TXT + Upload de provas</div>
        </div>
      </div>

      <div class="smos-actions">
        <div class="smos-keywrap">
          <span class="smos-badge">DEMO_KEY</span>
          <input id="demoKey" class="smos-input" type="text" placeholder="Cole a DEMO_KEY" autocomplete="off"/>
        </div>
        <button id="btnActivate" class="smos-btn smos-btn-primary">Ativar</button>
        <button id="btnReset" class="smos-btn">Reiniciar</button>
      </div>
    </header>

    <section class="smos-toolbar">
      <div class="smos-upload">
        <input id="fileInput" type="file" multiple />
        <button id="btnUpload" class="smos-btn">Subir provas</button>
        <div class="smos-tip">Dica: PDF/DOCX/TXT extração local. (Imagens podem custar mais.)</div>
      </div>

      <div class="smos-downloads">
        <div class="smos-dl-title">Downloads</div>
        <button id="dlReport" class="smos-btn smos-btn-dl" disabled>Baixar Relatório+Estratégia.docx</button>
        <button id="dlProp" class="smos-btn smos-btn-dl" disabled>Baixar Proposta.docx</button>
        <button id="dlPiece" class="smos-btn smos-btn-dl" disabled>Baixar Peça.docx</button>
      </div>
    </section>

    <main class="smos-main">
      <div class="smos-chat" id="chatBox" aria-live="polite"></div>

      <div class="smos-compose">
        <textarea id="msgInput" class="smos-textarea" rows="1" placeholder="Digite aqui… (Enter envia)"></textarea>
        <button id="btnSend" class="smos-btn smos-btn-primary">Enviar</button>
      </div>
    </main>
  </div>

  <!-- Loader -->
  <div id="loader" class="smos-loader" hidden>
    <div class="smos-loader-card">
      <div class="smos-loader-title">Gerando seus arquivos…</div>
      <div class="smos-loader-sub">Relatório + Proposta + Minuta (DOCX)</div>
      <div class="smos-loader-bar"><div class="smos-loader-barfill"></div></div>
    </div>
  </div>
</div>

<style>
  /* Hard reset inside container */
  #smos-app { position: relative; width: 100%; }
  #smos-app, #smos-app * { box-sizing: border-box; }

  /* Fullscreen background on WP page area */
  .smos-video-bg {
    position: fixed; inset: 0;
    z-index: 0;
    overflow: hidden;
    pointer-events: none;
  }
  .smos-video-bg iframe {
    position: absolute;
    top: 50%; left: 50%;
    width: 177.78vh; height: 100vh; /* cover */
    min-width: 100vw; min-height: 56.25vw;
    transform: translate(-50%, -50%);
    filter: saturate(1.05) contrast(1.05);
  }
  .smos-video-overlay{
    position: fixed; inset: 0;
    z-index: 1;
    pointer-events: none;
    background: radial-gradient(1200px 600px at 50% 0%, rgba(0,0,0,.35), rgba(0,0,0,.70));
  }

  /* Shell full height, no white space */
  .smos-shell{
    position: relative;
    z-index: 2;
    width: 100%;
    min-height: 100svh;
    padding: 14px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    color: #eaf2ff;
    font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
  }

  .smos-topbar{
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap: 12px;
    padding: 12px 14px;
    border-radius: 16px;
    background: rgba(8, 20, 38, .55);
    border: 1px solid rgba(255,255,255,.12);
    backdrop-filter: blur(10px);
  }

  .smos-brand{ display:flex; align-items:center; gap:12px; min-width: 240px;}
  .smos-logo{
    width: 44px; height:44px;
    border-radius: 12px;
    display:flex; align-items:center; justify-content:center;
    font-weight: 800;
    letter-spacing:.5px;
    background: rgba(0,0,0,.35);
    border: 1px solid rgba(255,204,64,.35);
    color: #ffcc40;
  }
  .smos-title .smos-h1{ font-weight:800; font-size: 16px; line-height: 1.2; }
  .smos-title .smos-h2{ opacity:.85; font-size: 12px; }

  .smos-actions{ display:flex; align-items:center; gap:10px; flex-wrap: wrap; justify-content:flex-end; }
  .smos-keywrap{ display:flex; align-items:center; gap:8px; padding: 6px 8px; border-radius: 12px; background: rgba(0,0,0,.25); border:1px solid rgba(255,255,255,.12);}
  .smos-badge{ font-size: 11px; font-weight: 700; color:#ffcc40; padding: 4px 8px; border-radius: 999px; border:1px solid rgba(255,204,64,.35); background: rgba(0,0,0,.25); }
  .smos-input{
    width: 260px; max-width: 60vw;
    background: transparent;
    border: none;
    outline: none;
    color: #eaf2ff;
    font-size: 13px;
  }

  .smos-btn{
    border: 1px solid rgba(255,255,255,.18);
    background: rgba(0,0,0,.25);
    color: #eaf2ff;
    padding: 10px 12px;
    border-radius: 12px;
    cursor: pointer;
    transition: transform .06s ease, background .15s ease;
    font-weight: 650;
    font-size: 13px;
    white-space: nowrap;
  }
  .smos-btn:hover{ background: rgba(0,0,0,.35); }
  .smos-btn:active{ transform: translateY(1px); }
  .smos-btn[disabled]{ opacity:.5; cursor:not-allowed; }

  .smos-btn-primary{
    background: rgba(255, 204, 64, .18);
    border-color: rgba(255, 204, 64, .40);
    color: #ffe7a3;
  }
  .smos-btn-dl{
    padding: 9px 10px;
    border-radius: 10px;
  }

  .smos-toolbar{
    display:flex;
    gap: 12px;
    flex-wrap: wrap;
    justify-content: space-between;
  }
  .smos-upload, .smos-downloads{
    flex: 1 1 320px;
    padding: 12px 14px;
    border-radius: 16px;
    background: rgba(8, 20, 38, .50);
    border: 1px solid rgba(255,255,255,.12);
    backdrop-filter: blur(10px);
    display:flex;
    align-items:center;
    gap: 10px;
    flex-wrap: wrap;
  }
  .smos-tip{ font-size: 12px; opacity: .85; }
  .smos-dl-title{ font-weight: 800; color:#ffcc40; margin-right: 6px; }

  .smos-main{
    flex: 1;
    min-height: 0;
    padding: 12px 14px;
    border-radius: 18px;
    background: rgba(8, 20, 38, .40);
    border: 1px solid rgba(255,255,255,.12);
    backdrop-filter: blur(10px);
    display: flex;
    flex-direction: column;
  }

  .smos-chat{
    flex: 1;
    min-height: 0;
    overflow: auto;
    padding: 12px 10px;
    border-radius: 14px;
    background: rgba(0,0,0,.18);
    border: 1px solid rgba(255,255,255,.10);
  }
  .smos-line{ margin: 6px 0; line-height: 1.45; font-size: 14px; }
  .smos-ia{ color: #dce9ff; }
  .smos-user{ color: #ffebb0; }

  .smos-compose{
    margin-top: 10px;
    display:flex;
    gap: 10px;
    align-items:flex-end;
    position: sticky;
    bottom: 0;
    padding-top: 10px;
    background: linear-gradient(to top, rgba(8,20,38,.75), rgba(8,20,38,0));
  }
  .smos-textarea{
    flex: 1;
    resize: none;
    min-height: 44px;
    max-height: 140px;
    padding: 10px 12px;
    border-radius: 14px;
    border: 1px solid rgba(255,255,255,.16);
    background: rgba(0,0,0,.25);
    color: #eaf2ff;
    outline: none;
    font-size: 14px;
    line-height: 1.3;
  }

  /* Loader overlay */
  .smos-loader{
    position: fixed; inset: 0;
    z-index: 9999;
    display:flex;
    align-items:center;
    justify-content:center;
    background: rgba(0,0,0,.55);
    backdrop-filter: blur(6px);
  }
  .smos-loader-card{
    width: min(520px, 92vw);
    padding: 18px;
    border-radius: 16px;
    border: 1px solid rgba(255,255,255,.16);
    background: rgba(10, 18, 30, .85);
    color:#eaf2ff;
  }
  .smos-loader-title{ font-size: 16px; font-weight: 900; color:#ffcc40; margin-bottom: 6px;}
  .smos-loader-sub{ font-size: 13px; opacity: .9; margin-bottom: 12px;}
  .smos-loader-bar{ height: 10px; border-radius: 999px; background: rgba(255,255,255,.12); overflow: hidden; }
  .smos-loader-barfill{
    height: 100%;
    width: 35%;
    background: rgba(255,204,64,.70);
    border-radius: 999px;
    animation: smosload 1.1s ease-in-out infinite alternate;
  }
  @keyframes smosload { from { width: 25%; } to { width: 85%; } }

  /* Mobile */
  @media (max-width: 860px){
    .smos-input{ width: 180px; }
    .smos-title .smos-h1{ font-size: 15px; }
    .smos-shell{ padding: 10px; }
  }
</style>

<script>
(() => {
  // ====== CONFIG ======
  const API_BASE = "https://sm-os-demo-backend.onrender.com"; // <-- tu backend render
  const DEMO_KEY_DEFAULT = "SMOS-DEMO-9f3a8c2b-2026"; // <-- tu demo key

  // ====== DOM ======
  const chatBox = document.getElementById("chatBox");
  const msgInput = document.getElementById("msgInput");
  const demoKeyInput = document.getElementById("demoKey");
  const btnActivate = document.getElementById("btnActivate");
  const btnReset = document.getElementById("btnReset");
  const btnSend = document.getElementById("btnSend");

  const fileInput = document.getElementById("fileInput");
  const btnUpload = document.getElementById("btnUpload");

  const dlReport = document.getElementById("dlReport");
  const dlProp = document.getElementById("dlProp");
  const dlPiece = document.getElementById("dlPiece");

  const loader = document.getElementById("loader");

  demoKeyInput.value = DEMO_KEY_DEFAULT;

  // ====== STATE ======
  let sessionId = null;
  let state = {};
  let downloads = { report:null, prop:null, piece:null };

  const setLoading = (on) => { loader.hidden = !on; };

  function addLine(text, who="ia"){
    const div = document.createElement("div");
    div.className = "smos-line " + (who === "user" ? "smos-user" : "smos-ia");
    div.textContent = text;
    chatBox.appendChild(div);
    chatBox.scrollTop = chatBox.scrollHeight;
  }

  function setDownload(btn, payload){
    if(!payload){ btn.disabled = true; btn.onclick = null; return; }
    btn.disabled = false;
    btn.onclick = () => {
      const bytes = Uint8Array.from(atob(payload.b64), c => c.charCodeAt(0));
      const blob = new Blob([bytes], {type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document"});
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = payload.name || "arquivo.docx";
      document.body.appendChild(a);
      a.click();
      setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 250);
    };
  }

  async function api(path, body){
    const key = demoKeyInput.value.trim();
    const res = await fetch(API_BASE + path, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-demo-key": key
      },
      body: JSON.stringify(body || {})
    });
    const txt = await res.text();
    let data = null;
    try { data = JSON.parse(txt); } catch(e){ data = { raw: txt }; }
    if(!res.ok){
      throw new Error((data && data.detail) ? data.detail : ("HTTP " + res.status));
    }
    return data;
  }

  async function startSession(){
    setLoading(true);
    try{
      const key = demoKeyInput.value.trim();
      const res = await fetch(API_BASE + "/session/new", { method: "POST", headers: {"x-demo-key": key} });
      const data = await res.json();
      sessionId = data.session_id;
      state = data.state || {};
      downloads = { report:null, prop:null, piece:null };
      setDownload(dlReport, null);
      setDownload(dlProp, null);
      setDownload(dlPiece, null);
      chatBox.innerHTML = "";
      addLine(data.message, "ia");
    } finally {
      setLoading(false);
    }
  }

  async function sendMessage(){
    const msg = msgInput.value.trim();
    if(!msg) return;

    addLine("Você: " + msg, "user");
    msgInput.value = "";
    msgInput.style.height = "44px";

    setLoading(true);
    try{
      const data = await api("/chat", { session_id: sessionId, message: msg, state });

      state = data.state || state;
      addLine("IA: " + (data.message || ""), "ia");

      // downloads
      if(data.report_docx_b64){
        downloads.report = { b64: data.report_docx_b64, name: data.report_docx_filename || "Relatorio.docx" };
        setDownload(dlReport, downloads.report);
      }
      if(data.proposal_docx_b64){
        downloads.prop = { b64: data.proposal_docx_b64, name: data.proposal_docx_filename || "Proposta.docx" };
        setDownload(dlProp, downloads.prop);
      }
      if(data.piece_docx_b64){
        downloads.piece = { b64: data.piece_docx_b64, name: data.piece_docx_filename || "Peca.docx" };
        setDownload(dlPiece, downloads.piece);
      }
    } catch(err){
      addLine("⚠️ Falha: " + err.message, "ia");
    } finally {
      setLoading(false);
    }
  }

  // Auto-resize textarea
  msgInput.addEventListener("input", () => {
    msgInput.style.height = "44px";
    msgInput.style.height = Math.min(msgInput.scrollHeight, 140) + "px";
  });

  // Enter send (Shift+Enter newline)
  msgInput.addEventListener("keydown", (e) => {
    if(e.key === "Enter" && !e.shiftKey){
      e.preventDefault();
      sendMessage();
    }
  });

  btnSend.addEventListener("click", sendMessage);
  btnActivate.addEventListener("click", startSession);
  btnReset.addEventListener("click", startSession);

  // Upload
  btnUpload.addEventListener("click", async () => {
    if(!sessionId){ addLine("IA: Ative a sessão antes de subir provas.", "ia"); return; }
    const files = [...fileInput.files];
    if(!files.length){ addLine("IA: Selecione ao menos 1 arquivo.", "ia"); return; }

    setLoading(true);
    try{
      for(const f of files){
        const b64 = await new Promise((resolve, reject) => {
          const r = new FileReader();
          r.onload = () => resolve(String(r.result).split(",")[1]);
          r.onerror = reject;
          r.readAsDataURL(f);
        });

        const out = await api("/upload", { session_id: sessionId, filename: f.name, mime: f.type || "application/octet-stream", b64 });
        addLine(`IA: ✅ Prova recebida: ${out.filename} (extração: ${out.text_extracted ? "sim" : "não"})`, "ia");
      }
      fileInput.value = "";
    } catch(err){
      addLine("⚠️ Upload falhou: " + err.message, "ia");
    } finally {
      setLoading(false);
    }
  });

  // Boot (no auto start)
  addLine("Cole a DEMO_KEY e clique em Ativar.", "ia");
})();
</script>
