/* Todas las Voces — comportamiento de la interfaz (sin dependencias). */
(function () {
  "use strict";
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

  /* Menú en móvil */
  const menuBtn = $(".menu-btn");
  if (menuBtn) {
    menuBtn.addEventListener("click", () => {
      const open = $(".nav").classList.toggle("open");
      menuBtn.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  /* Reproductor: un solo audio a la vez; el botón pertenece a un elemento con la huella de la voz */
  const audio = new Audio();
  audio.preload = "none";
  let current = null;
  function stop() {
    if (current) {
      current.classList.remove("is-playing");
      const holder = current.closest("[data-player]");
      if (holder) holder.classList.remove("is-playing");
    }
    current = null;
  }
  audio.addEventListener("ended", stop);
  audio.addEventListener("pause", () => { if (audio.ended || audio.currentTime === 0) stop(); });
  audio.addEventListener("error", () => { stop(); });
  function play(btn) {
    if (current === btn) { audio.pause(); audio.currentTime = 0; stop(); return; }
    stop();
    current = btn;
    btn.classList.add("is-playing");
    const holder = btn.closest("[data-player]");
    if (holder) holder.classList.add("is-playing");
    audio.src = btn.dataset.src;
    audio.play().catch(stop);
  }
  document.addEventListener("click", (ev) => {
    const btn = ev.target.closest(".play[data-src]");
    if (btn) { ev.preventDefault(); play(btn); }
  });

  /* Confirmación de acciones delicadas y botones que tardan */
  document.addEventListener("submit", (ev) => {
    const form = ev.target;
    const msg = form.dataset.confirm || (ev.submitter && ev.submitter.dataset.confirm);
    if (msg && !window.confirm(msg)) { ev.preventDefault(); return; }
    const busy = ev.submitter && ev.submitter.dataset.busy;
    if (busy) {
      const btn = ev.submitter;
      setTimeout(() => { btn.classList.add("is-busy"); btn.disabled = true; btn.textContent = busy; }, 0);
    }
  });

  /* Contador de caracteres */
  $$("textarea[data-count]").forEach((ta) => {
    const out = document.createElement("div");
    out.className = "help";
    ta.insertAdjacentElement("afterend", out);
    const max = parseInt(ta.getAttribute("maxlength") || "0", 10);
    const update = () => { out.textContent = ta.value.length.toLocaleString("en-US") + (max ? " / " + max.toLocaleString("en-US") : ""); };
    ta.addEventListener("input", update);
    update();
  });

  function csrf() {
    const m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? m[1] : ($("[name=csrfmiddlewaretoken]") || {}).value || "";
  }

  /* Demo de texto libre en la ficha de una voz */
  const demo = $("#demo-form");
  if (demo) {
    const out = $("#demo-out");
    const btn = $("button", demo);
    demo.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const label = btn.textContent;
      btn.disabled = true; btn.textContent = btn.dataset.busyText || "…";
      out.textContent = "";
      try {
        const resp = await fetch(demo.action, {
          method: "POST", headers: { "X-CSRFToken": csrf(), "X-Requested-With": "fetch" },
          body: new FormData(demo), credentials: "same-origin",
        });
        const data = await resp.json();
        if (data.ok) {
          const play_btn = $("[data-player] .play", demo);
          play_btn.dataset.src = data.url + "?t=" + Date.now();
          play_btn.closest("[data-player]").classList.remove("hide");
          play(play_btn);
        } else {
          out.textContent = data.error || "Error";
        }
      } catch (err) {
        out.textContent = demo.dataset.error || "Error";
      }
      btn.disabled = false; btn.textContent = label;
    });
  }

  /* Presupuesto en vivo en el formulario de solicitud */
  const quote = $("#quote");
  if (quote) {
    const form = quote.closest("form");
    const refresh = async () => {
      const params = new URLSearchParams();
      ["category", "territory", "duration_months"].forEach((n) => {
        const el = form.elements[n];
        if (el) params.set(n, el.value);
      });
      try {
        const data = await (await fetch(quote.dataset.url + "?" + params, { credentials: "same-origin" })).json();
        if (data.ok) {
          $("[data-net]", quote).textContent = data.net;
          $("[data-total]", quote).textContent = data.total;
        }
      } catch (err) { /* el presupuesto se recalcula igualmente en el servidor */ }
    };
    ["category", "territory", "duration_months"].forEach((n) => {
      if (form.elements[n]) form.elements[n].addEventListener("change", refresh);
    });
    refresh();
  }

  /* Grabación en directo de la frase de titularidad */
  const rec = $("#recorder");
  if (rec) {
    const startBtn = $("[data-start]", rec), stopBtn = $("[data-stop]", rec), sendBtn = $("[data-send]", rec);
    const status = $("[data-status]", rec), preview = $("audio", rec), fileInput = $("input[type=file]", rec);
    let recorder = null, chunks = [], timer = null, seconds = 0;
    if (!navigator.mediaDevices || !window.MediaRecorder) {
      $("[data-live]", rec).classList.add("hide");
      $("[data-fallback]", rec).classList.remove("hide");
    }
    startBtn && startBtn.addEventListener("click", async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
        });
        chunks = []; seconds = 0;
        recorder = new MediaRecorder(stream);
        recorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
        recorder.onstop = () => {
          stream.getTracks().forEach((t) => t.stop());
          const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
          const ext = (blob.type.includes("mp4") || blob.type.includes("aac")) ? "m4a" : (blob.type.includes("ogg") ? "ogg" : "webm");
          const dt = new DataTransfer();
          dt.items.add(new File([blob], "frase." + ext, { type: blob.type }));
          fileInput.files = dt.files;
          preview.src = URL.createObjectURL(blob);
          preview.classList.remove("hide");
          sendBtn.classList.remove("hide");
          rec.classList.remove("is-recording");
        };
        recorder.start();
        rec.classList.add("is-recording");
        startBtn.classList.add("hide"); stopBtn.classList.remove("hide");
        timer = setInterval(() => {
          seconds += 1; status.textContent = "0:" + String(seconds).padStart(2, "0");
          if (seconds >= 40) stopBtn.click();
        }, 1000);
      } catch (err) {
        status.textContent = rec.dataset.micError || "Micrófono no disponible";
        $("[data-fallback]", rec).classList.remove("hide");
      }
    });
    stopBtn && stopBtn.addEventListener("click", () => {
      clearInterval(timer);
      if (recorder && recorder.state !== "inactive") recorder.stop();
      stopBtn.classList.add("hide"); startBtn.classList.remove("hide");
    });
  }

  /* Marcar/desmarcar todas las categorías no delicadas */
  const allBtn = $("[data-check-all]");
  if (allBtn) {
    allBtn.addEventListener("click", () => {
      $$("input[type=checkbox][data-regular]").forEach((c) => { c.checked = true; });
    });
  }
})();
