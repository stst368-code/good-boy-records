export function initGeneration({ api, toast, browse }) {
  const root = document.getElementById("generationView");
  root.innerHTML = `
  <div class="section-heading"><h1>Generation</h1><p class="subtle">Choose writing YAMLs, check the batch, then generate into the review inbox.</p></div>
  <div class="generation-grid">
    <section class="generation-panel"><h2>Tracks</h2>
      <label>Writing catalogue <div class="generation-inline"><input id="genRoot"><button id="genBrowse" type="button">Browse</button><button id="genScan" type="button">Load</button></div></label>
      <div class="generation-inline"><label class="flex">Find a song or file<input id="genSearch" type="search"></label><label>Album<select id="genAlbum"><option value="">All albums</option></select></label></div>
      <div class="generation-inline"><button id="genSelectVisible" type="button">Select shown</button><button id="genClear" type="button">Clear</button><span id="genSelected" class="subtle small"></span></div>
      <div id="genFiles" class="generation-files" aria-label="Writing inputs"></div>
      <details><summary>Selection and order</summary><label>Run order<select id="genOrder"><option value="selection">Selection order</option><option value="filename">Filename</option><option value="album">Album track order</option></select></label>
        <label class="generation-check"><input id="genLowRated" type="checkbox"> Only tracks with low saved ratings</label>
        <div class="generation-fields"><label>Mean rating at or below<input id="genRatingMax" type="number" min="0" max="5" step="0.1" value="3"></label><label>Review cluster<input id="genRatingCluster" placeholder="Any cluster"></label><label>Review style<input id="genRatingStyle" placeholder="Any style"></label></div>
        <p class="subtle small">Uses latest saved reviews. Unreviewed tracks are excluded when this filter is enabled.</p>
      </details>
      <h2>Generation controls</h2><div class="generation-fields"><label>Maximum seconds<input id="genDuration" type="number" min="0.04" max="360" value="360"></label><label>Diffusion steps<input id="genSteps" type="number" min="1" max="1000" value="30"></label><label>Diffusion CFG<input id="genCfg" type="number" step="0.05" min="0" value="1.7"></label><label>Settings priority<select id="genPriority"><option value="yaml">YAML values first</option><option value="form">Use these controls</option></select></label></div>
      <label>Caption template<input id="genTemplate" list="genTemplateList" placeholder="Optional template ID; leave blank for random captions"><datalist id="genTemplateList"></datalist></label>
      <div class="generation-fields"><label>Caption selection<select id="genCaptionMode"><option value="random">Random templates</option><option value="yaml">Use YAML caption</option><option value="catalogue">Use catalogue style</option></select></label><label>Random captions per track<input id="genCaptionCount" type="number" min="1" max="1000" value="1"></label></div>
      <details><summary>Caption pool</summary><div class="generation-fields"><label>Cluster<input id="genCaptionCluster" placeholder="Any cluster"></label><label>Style<input id="genCaptionStyle" placeholder="Any style"></label></div><p class="subtle small">Random captions are unique within each track's selection. Every chosen template is frozen in the preview.</p></details>
      <details><summary>Experiment quantities and ranges</summary><div class="generation-fields"><label>Generations per combination<input id="genRepetitions" type="number" min="1" max="10000" value="1"></label><label>Combinations<select id="genStrategy"><option value="grid">Every combination</option><option value="sample">Random value combinations</option></select></label><label>Random combinations<input id="genSamples" type="number" min="1" max="10000" value="1"></label><label>Seeds for repeated runs<select id="genSeedPolicy"><option value="randomize">New automatic seeds per run</option><option value="preserve">Use resolved seeds</option></select></label></div><div id="genSweeps"></div><button id="genAddSweep" type="button">+ Parameter range / list</button><p class="subtle small">Use comma-separated values or inclusive start:stop:step ranges, e.g. 1.2:2.0:0.2. Sweep values override YAML/form priorities; review rerun overrides remain final. Total = tracks × captions × combinations × generations. Limit: 10,000.</p></details>
      <details><summary>Precision, seeds and parameter precedence</summary><div class="generation-fields"><label>Text encoder<select id="genEncoder"><option>bf16</option><option>fp32</option></select></label><label>Diffusion precision<select id="genDit"><option>bf16</option><option>fp32</option></select></label><label>Encoder seed<input id="genEncoderSeed" value="auto"></label><label>Diffusion seed<input id="genSamplerSeed" value="auto"></label></div>
        <label>Model<input id="genModel" value="MiniMaxAI/MiniMax-Music3"></label><label>Model revision<input id="genRevision" value="fbdf52fbaaca799592917417eb05f1899f1255ec"></label>
        <div class="generation-fields"><label>Encoder CFG<input id="genEncoderCfg" type="number" min="0" step="0.05" value="1.5"></label><label>Encoder top-k<input id="genTopK" type="number" min="1" max="16384" value="50"></label><label>Sampler<select id="genSampler"><option>euler</option><option>heun</option><option>midpoint</option><option>rk4</option><option>dpmpp_2m</option></select></label><label>Schedule<select id="genSchedule"><option>flow_match_euler</option><option>linear</option><option>karras</option><option>exponential</option><option>cosine</option></select></label></div><p class="subtle small">MiniMax flow samplers use time from noise to audio. Heun, midpoint and RK4 need extra model evaluations; alternative solvers and schedules are experimental. Automatic seeds are frozen separately for every run.</p>
        <label>Per-field precedence (optional JSON, lowest priority first)<textarea id="genPrecedence" rows="2" placeholder='{"sampler_seed":["defaults","yaml","batch","rerun"]}'></textarea></label>
      </details>
      <div class="generation-fields"><label>Conditioning cache<select id="genCache"><option value="prefer">Reuse compatible encodings</option><option value="require">Require cached encodings</option><option value="disabled">Encode every time</option></select></label><label>Output grouping<select id="genLayout"><option value="single">Single inbox</option><option value="track">Group by track</option><option value="album">Group by album</option></select></label></div>
      <p id="genOutput" class="subtle small"></p><button id="genPreview" class="primary" type="button">Check selected tracks</button>
    </section>
    <section class="generation-panel"><h2>Worker</h2>
      <label>Saved connection<select id="genSaved"><option value="">New connection</option></select></label>
      <label>Run on<select id="genBackend"><option value="local">This PC</option><option value="runpod_dedicated">RunPod dedicated pod</option><option value="runpod_serverless">RunPod Serverless</option></select></label>
      <label id="genAddressWrap">Connection<input id="genAddress" placeholder="Paste the direct SSH command or ssh://root@IP:PORT"></label>
      <label id="genIdentityWrap">Private key on this PC<input id="genIdentity" list="genKeyList" placeholder="Choose the private key registered in RunPod"><datalist id="genKeyList"></datalist></label>
      <div id="genServerless" hidden><label>RunPod API key <span class="subtle small">Session only; blank uses RUNPOD_API_KEY</span><input id="genApiKey" type="password" autocomplete="new-password"></label>
        <details><summary>Private S3 / R2 result transfer</summary><label>Storage endpoint<input id="genStorageEndpoint" placeholder="https://ACCOUNT.r2.cloudflarestorage.com"></label><label>Bucket<input id="genStorageBucket"></label><label>Region<input id="genStorageRegion" value="auto"></label><label>Access key ID<input id="genStorageAccess" type="password" autocomplete="new-password"></label><label>Secret access key<input id="genStorageSecret" type="password" autocomplete="new-password"></label><p class="subtle small">Keys stay in this app session; environment credentials are also supported. Verified results are downloaded and removed from temporary storage.</p></details>
        <details><summary>First endpoint deployment</summary><p>Connect this repository in RunPod Serverless and build <code>_99_tools/_workers_infdit/Dockerfile.serverless</code> with the repository root as the build context. Select a 48 GB GPU and use a persistent volume for model weights and encodings. Then paste its endpoint ID above.</p><p class="subtle small">Worker hardware is detected when a job starts. The connection check reads endpoint health without starting a generation.</p></details>
      </div>
      <label>Memory mode<select id="genMemory"><option value="auto">Automatic for this run mode</option><option value="low_vram">CPU offload for smaller GPUs</option><option value="resident_stage">Keep the active stage on GPU</option></select></label>
      <label class="generation-check"><input id="genAutoSetup" type="checkbox" checked> Prepare or reuse the worker automatically</label>
      <div class="generation-inline"><button id="genSaveWorker" type="button">Save connection</button><button id="genCheckWorker" type="button">Check connection</button><button id="genPrepareWorker" type="button">Prepare worker</button></div>
      <p id="genConnectionStatus" class="subtle small" role="status"></p>
      <section id="genPlan" class="generation-plan" hidden><h2>Batch check</h2><p id="genPlanSummary"></p><div id="genPlanRows"></div><label class="generation-check"><input id="genDropErrors" type="checkbox"> Drop errored inputs and run the valid tracks</label><button id="genStart" class="primary" type="button">Generate checked tracks</button><button id="genCancelPreview" type="button">Cancel batch</button></section>
      <h2>Jobs</h2><button id="genRefreshJobs" class="quiet-button" type="button">Refresh</button><div id="genJobs" class="generation-jobs"></div>
    </section>
  </div>`;
  const $ = (id) => document.getElementById(id);
  const state = {
    rows: [],
    selected: new Set(),
    catalogueRoot: null,
    scanSequence: 0,
    connections: [],
    token: null,
    preview: null,
    loaded: false,
    poll: null,
    busy: false,
  };
  const guarded =
    (fn) =>
    (...args) =>
      Promise.resolve(fn(...args)).catch((error) => toast(error.message));
  function connection() {
    return {
      backend: $("genBackend").value,
      address:
        $("genBackend").value === "local" ? "" : $("genAddress").value.trim(),
      identity_file:
        $("genBackend").value === "runpod_dedicated"
          ? $("genIdentity").value.trim()
          : "",
      memory_profile: $("genMemory").value,
      auto_setup: $("genAutoSetup").checked,
    };
  }
  function parameters() {
    return {
      model: $("genModel").value.trim(),
      dit: $("genDit").value,
      text_encoder: $("genEncoder").value,
      encoder_cfg: Number($("genEncoderCfg").value),
      top_k: Number($("genTopK").value),
      sampler: $("genSampler").value,
      scheduler: $("genSchedule").value,
      sampler_cfg: Number($("genCfg").value),
      sampler_steps: Number($("genSteps").value),
      duration: Number($("genDuration").value),
      encoder_seed: $("genEncoderSeed").value.trim() || "auto",
      sampler_seed: $("genSamplerSeed").value.trim() || "auto",
    };
  }
  function experiments() {
    const sweeps = {};
    for (const row of $("genSweeps").children) {
      const field = row.querySelector("select").value, value = row.querySelector("input").value.trim();
      if (!value) continue;
      if (field in sweeps) throw new Error("Use one range/list per parameter.");
      sweeps[field] = value;
    }
    return {repetitions: Number($("genRepetitions").value), strategy: $("genStrategy").value,
      samples: Number($("genSamples").value), seed_policy: $("genSeedPolicy").value, sweeps};
  }
  $("genAddSweep").onclick = () => {
    const row = document.createElement("div"); row.className = "generation-inline";
    const field = document.createElement("select"); field.setAttribute("aria-label", "Sweep parameter");
    for (const key of ["sampler_cfg", "sampler_steps", "encoder_cfg", "top_k", "sampler", "scheduler", "duration", "encoder_seed", "sampler_seed", "dit", "text_encoder"]) field.append(new Option(key, key));
    const value = document.createElement("input"); value.placeholder = "1.2:2.0:0.2 or 1.2, 1.5, 1.8"; value.setAttribute("aria-label", "Sweep values");
    const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "Remove"; remove.onclick = () => {row.remove(); invalidate();};
    row.append(field, value, remove); row.oninput = invalidate; row.onchange = invalidate; $("genSweeps").append(row); invalidate();
  };
  function preferences() {
    return {
      connection: connection(),
      input_root: $("genRoot").value,
      parameters: parameters(),
      layout: $("genLayout").value,
      storage_endpoint: $("genStorageEndpoint").value.trim(),
      storage_bucket: $("genStorageBucket").value.trim(),
      storage_region: $("genStorageRegion").value.trim() || "auto",
      secrets: {
        api_key: $("genApiKey").value,
        s3_access_key: $("genStorageAccess").value,
        s3_secret_key: $("genStorageSecret").value,
      },
    };
  }
  function invalidate() {
    state.token = null;
    state.preview = null;
    $("genPlan").hidden = true;
  }
  function backendFields() {
    const mode = $("genBackend").value;
    $("genAddressWrap").hidden = mode === "local";
    $("genIdentityWrap").hidden = mode !== "runpod_dedicated";
    $("genServerless").hidden = mode !== "runpod_serverless";
    $("genPrepareWorker").hidden = mode === "runpod_serverless";
    $("genAutoSetup").closest("label").hidden = mode === "runpod_serverless";
    $("genAddress").placeholder =
      mode === "runpod_serverless"
        ? "Endpoint ID or https://api.runpod.ai/v2/ENDPOINT"
        : "Direct SSH command or ssh://root@IP:PORT";
    invalidate();
  }
  function fillConnection(value) {
    $("genBackend").value = value.backend;
    $("genAddress").value = value.address || "";
    $("genIdentity").value = value.identity_file || $("genIdentity").value;
    $("genMemory").value = value.memory_profile || "auto";
    $("genAutoSetup").checked = value.auto_setup !== false;
    backendFields();
  }
  async function save() {
    const result = await api("/generation/preferences", preferences());
    state.connections = result.connections;
    paintConnections();
    $("genApiKey").value = "";
    $("genStorageAccess").value = "";
    $("genStorageSecret").value = "";
    $("genApiKey").placeholder = result.has_api_key
      ? "Configured for this session"
      : "";
    $("genStorageSecret").placeholder = result.has_storage_key
      ? "Configured for this session"
      : "";
    return result;
  }
  function paintConnections() {
    const current = connection();
    $("genSaved").replaceChildren(new Option("New connection", ""));
    for (const [index, item] of state.connections.entries()) {
      const label =
        item.backend === "local"
          ? "This PC"
          : `${item.backend === "runpod_dedicated" ? "Pod" : "Serverless"} · ${item.address}`;
      $("genSaved").append(new Option(label, String(index)));
      if (item.backend === current.backend && item.address === current.address)
        $("genSaved").value = String(index);
    }
  }
  function visible() {
    const search = $("genSearch").value.trim().toLowerCase(),
      album = $("genAlbum").value;
    return state.rows.filter(
      (row) =>
        (!album || row.album === album) &&
        (!search ||
          `${row.title} ${row.relative} ${row.song_id} ${row.variant_id}`
            .toLowerCase()
            .includes(search)),
    );
  }
  function paintFiles() {
    const shown = visible();
    $("genFiles").replaceChildren();
    for (const row of shown) {
      const label = document.createElement("label");
      label.className = "generation-file";
      const check = document.createElement("input");
      check.type = "checkbox";
      check.checked = state.selected.has(row.relative);
      check.onchange = () => {
        if (check.checked) state.selected.add(row.relative);
        else state.selected.delete(row.relative);
        invalidate();
        paintSelected();
      };
      const text = document.createElement("span");
      const title = document.createElement("strong");
      title.textContent = row.title;
      const detail = document.createElement("small");
      detail.textContent = `${row.relative} · song ${row.song_id || "?"} / variant ${row.variant_id || "?"}${row.issues.length ? " · catalogue issue" : ""}`;
      text.append(title, detail);
      label.append(check, text);
      $("genFiles").append(label);
    }
    paintSelected();
  }
  function paintSelected() {
    $("genSelected").textContent =
      `${state.selected.size} selected · ${visible().length} shown / ${state.rows.length}`;
  }
  function clearCatalogue() {
    state.scanSequence++;
    state.catalogueRoot = null;
    state.rows = [];
    state.selected = new Set();
    $("genAlbum").replaceChildren(new Option("All albums", ""));
    invalidate();
    paintFiles();
  }
  async function scan() {
    clearCatalogue();
    const sequence = state.scanSequence, requestedRoot = $("genRoot").value.trim();
    const result = await api(
      "/generation/catalogue?" +
        new URLSearchParams({ root: requestedRoot }),
    );
    if (sequence !== state.scanSequence || $("genRoot").value.trim() !== requestedRoot) return;
    $("genRoot").value = result.root;
    state.catalogueRoot = result.root;
    state.rows = result.inputs;
    state.selected = new Set();
    $("genAlbum").replaceChildren(new Option("All albums", ""));
    for (const album of result.albums)
      $("genAlbum").append(new Option(album, album));
    invalidate();
    paintFiles();
  }
  function paintPlan() {
    const plan = state.preview;
    $("genPlan").hidden = !plan;
    if (!plan) return;
    $("genPlanSummary").textContent =
      `${plan.ready} ready · ${plan.generation_count ?? plan.ready} generations · ${plan.invalid} errored · ${connection().backend.replaceAll("_", " ")}. Seeds and source copies will be frozen before dispatch.`;
    $("genPlanRows").replaceChildren();
    for (const row of plan.inputs) {
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = `${row.status === "ready" ? "Ready" : "Error"} · ${row.path.split(/[\\/]/).pop()}`;
      const text = document.createElement("pre");
      text.textContent = [
        ...row.issues.map((issue) => `${issue.severity}: ${issue.message}`),
        JSON.stringify(
          { controls: row.effective, from: row.provenance, generations: row.generation_count, experiments: row.experiments },
          null,
          2,
        ),
      ].join("\n");
      details.append(summary, text);
      $("genPlanRows").append(details);
    }
    $("genDropErrors").closest("label").hidden = plan.invalid === 0;
    $("genStart").disabled =
      !plan.ready || Boolean(plan.invalid && !$("genDropErrors").checked);
  }
  async function preview() {
    if (!state.catalogueRoot || $("genRoot").value.trim() !== state.catalogueRoot)
      throw new Error("Load the writing catalogue before checking tracks.");
    if (!state.selected.size)
      throw new Error("Select one or more writing YAMLs first.");
    const loadedRoot = state.catalogueRoot, files = [...state.selected];
    await save();
    if (loadedRoot !== state.catalogueRoot || $("genRoot").value.trim() !== loadedRoot)
      throw new Error("Writing folder changed. Load it again before checking tracks.");
    const body = {
      input_root: loadedRoot,
      connection: connection(),
      files,
      order: $("genOrder").value,
      parameters: parameters(),
      parameter_priority: $("genPriority").value,
      template_id: $("genTemplate").value.trim() || null,
      caption_mode: $("genCaptionMode").value,
      caption_count: $("genTemplate").value.trim() || $("genCaptionMode").value !== "random" ? 1 : Number($("genCaptionCount").value),
      caption_cluster: $("genCaptionCluster").value.trim() || null,
      caption_style: $("genCaptionStyle").value.trim() || null,
      experiments: experiments(),
      model_revision: $("genRevision").value.trim() || null,
      encoding_cache: $("genCache").value,
      layout: $("genLayout").value,
      field_precedence: $("genPrecedence").value.trim()
        ? JSON.parse($("genPrecedence").value)
        : {},
      rating_filter: $("genLowRated").checked
        ? {
            threshold: Number($("genRatingMax").value),
            cluster: $("genRatingCluster").value.trim() || null,
            style: $("genRatingStyle").value.trim() || null,
          }
        : null,
    };
    const result = await api("/generation/preview", body);
    if (loadedRoot !== state.catalogueRoot || $("genRoot").value.trim() !== loadedRoot) return;
    state.preview = result;
    state.token = state.preview.token;
    $("genDropErrors").checked = false;
    paintPlan();
  }
  async function jobs() {
    const expanded = new Set(
      [...$("genJobs").querySelectorAll("details[open]")].map(
        (item) => item.dataset.task,
      ),
    );
    const result = await api("/generation/tasks");
    $("genJobs").replaceChildren();
    for (const task of result.tasks) {
      const card = document.createElement("article");
      card.className = "generation-job";
      const heading = document.createElement("h3");
      heading.textContent = `${task.batch_id ? "Batch " + task.batch_id.slice(0, 8) : "Worker check"} · ${task.status.replaceAll("_", " ")}`;
      const detail = document.createElement("p");
      detail.className = "subtle small";
      const runs = task.runs || [];
      detail.textContent = `${task.connection.backend.replaceAll("_", " ")}${task.connection.address ? " · " + task.connection.address : ""}${runs.length ? " · " + runs.filter((run) => run.status === "succeeded").length + " / " + runs.length + " complete" : ""}`;
      card.append(heading, detail);
      if (task.error) {
        const error = document.createElement("p");
        error.className = "error";
        error.textContent = task.error;
        card.append(error);
      }
      if (task.report?.message) {
      const message = document.createElement("p");
      message.className = "subtle small";
      message.textContent = task.report.message;
      card.append(message);
    }
    const hardware = task.report?.hardware;
      if (hardware) {
        const info = document.createElement("p");
        info.className = "subtle small";
        info.textContent = `${hardware.gpu_name || "GPU unavailable"} · ${hardware.usable_vram_bytes ? (hardware.usable_vram_bytes / 1024 ** 3).toFixed(1) + " GiB VRAM" : "VRAM not reported"}`;
        card.append(info);
      }
      const actions = document.createElement("div");
      actions.className = "generation-inline";
      const options = [];
      if (task.active && task.batch_id && task.status !== "cleaning")
        options.push(["pause", "Pause after this song"]);
      if (!task.active) {
        if (!task.batch_id) options.push(["continue", "Run check again"]);
        else if (runs.some((run) => run.status === "running")) {
          if (task.connection.backend !== "local")
            options.push(["resume", "Reconnect to existing job"]);
          else {
            const note = document.createElement("p");
            note.className = "subtle small";
            note.textContent =
              "The local controller stopped. Check its log and recover the interrupted attempt before retrying.";
            card.append(note);
          }
        } else if (runs.some((run) => run.status === "failed"))
          options.push(["retry", "Retry failed tracks"]);
        else if (runs.some((run) => run.status === "prepared"))
          options.push(["continue", "Continue queue"]);
        if (task.cleanup_pending)
          options.push(["cleanup", "Clean temporary files"]);
      }
      for (const [action, label] of options) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = label;
        button.onclick = guarded(async () => {
          await api(`/generation/tasks/${task.id}`, { action });
          await jobs();
        });
        actions.append(button);
      }
      card.append(actions);
      if (task.outputs?.length) {
        const location = document.createElement("p");
        location.className = "generation-output-path";
        location.textContent =
          task.outputs.find((item) => item.kind === "audio")?.uri || "";
        card.append(location);
      }
      if (task.log) {
        const log = document.createElement("details");
        log.dataset.task = task.id;
        log.open = expanded.has(task.id);
        const summary = document.createElement("summary");
        summary.textContent = "Progress log";
        const text = document.createElement("pre");
        text.textContent = task.log;
        log.append(summary, text);
        card.append(log);
      }
      $("genJobs").append(card);
    }
    if (!result.tasks.length)
      $("genJobs").textContent = "No generation jobs yet.";
  }
  async function worker(install) {
    await save();
    await api("/generation/worker", { ...preferences(), install });
    $("genConnectionStatus").textContent = install
      ? "Preparing worker; progress appears below."
      : "Checking connection; progress appears below.";
    await jobs();
  }
  $("genBackend").onchange = backendFields;
  $("genSaved").onchange = () => {
    if ($("genSaved").value !== "")
      fillConnection(state.connections[Number($("genSaved").value)]);
  };
  $("genBrowse").onclick = guarded(() =>
    browse((path) => {
      $("genRoot").value = path;
      invalidate();
    }),
  );
  $("genScan").onclick = guarded(scan);
  $("genSearch").oninput = paintFiles;
  $("genAlbum").onchange = paintFiles;
  $("genSelectVisible").onclick = () => {
    for (const row of visible()) state.selected.add(row.relative);
    invalidate();
    paintFiles();
  };
  $("genClear").onclick = () => {
    state.selected.clear();
    invalidate();
    paintFiles();
  };
  $("genSaveWorker").onclick = guarded(async () => {
    await save();
    toast("Connection saved.");
  });
  $("genCheckWorker").onclick = guarded(() => worker(false));
  $("genPrepareWorker").onclick = guarded(() => worker(true));
  $("genPreview").onclick = guarded(preview);
  $("genDropErrors").onchange = paintPlan;
  $("genCancelPreview").onclick = invalidate;
  $("genRefreshJobs").onclick = guarded(jobs);
  $("genStart").onclick = guarded(async () => {
    if (!state.token || state.busy) return;
    state.busy = true;
    $("genStart").disabled = true;
    try {
      await api("/generation/start", {
        token: state.token,
        skip_errors: $("genDropErrors").checked,
      });
      invalidate();
      await jobs();
      toast("Batch queued. You can continue reviewing.");
    } finally {
      state.busy = false;
    }
  });
  $("genRoot").addEventListener("input", clearCatalogue);
  for (const element of root.querySelectorAll("input,select,textarea"))
    if (
      ![
        "genSearch",
        "genAlbum",
        "genSaved",
        "genDropErrors",
        "genApiKey",
        "genStorageAccess",
        "genStorageSecret",
      ].includes(element.id)
    )
      element.addEventListener("input", invalidate);
  return {
    async open() {
      if (!state.loaded) {
        const prefs = await api("/generation/preferences");
        $("genRoot").value = prefs.input_root;
        $("genOutput").textContent =
          `Inbox: ${prefs.output_root}. Each generation keeps its FLAC, completed YAML, source and report together until review.`;
        state.connections = prefs.connections;
        for (const key of prefs.key_candidates)
          $("genKeyList").append(new Option(key, key));
        if (prefs.key_candidates.length)
          $("genIdentity").value = prefs.key_candidates[0];
        fillConnection(state.connections.at(-1) || { backend: "local" });
        paintConnections();
        for (const [id, key] of [
          ["genDuration", "duration"],
          ["genSteps", "sampler_steps"],
          ["genCfg", "sampler_cfg"],
          ["genEncoderCfg", "encoder_cfg"], ["genTopK", "top_k"], ["genSampler", "sampler"], ["genSchedule", "scheduler"],
          ["genEncoder", "text_encoder"],
          ["genDit", "dit"],
          ["genEncoderSeed", "encoder_seed"],
          ["genSamplerSeed", "sampler_seed"],
          ["genModel", "model"],
        ])
          $(id).value = prefs.parameters[key] ?? $("" + id).value;
        $("genLayout").value = prefs.layout;
        $("genStorageEndpoint").value = prefs.storage_endpoint;
        $("genStorageBucket").value = prefs.storage_bucket;
        $("genStorageRegion").value = prefs.storage_region;
        const templates = await api("/generation/templates");
        for (const item of templates.templates) {
          const option = new Option(
            `${item.cluster} / ${item.style} · ${item.source || ""}`,
            item.id,
          );
          $("genTemplateList").append(option);
        }
        state.loaded = true;
        if (!prefs.shared_database_ready)
          toast(
            "Generation needs the shared platform database. Open GBR through _97_app.",
          );
        await scan();
      }
      await jobs();
      clearInterval(state.poll);
      state.poll = setInterval(() => {
        if (!root.hidden) jobs().catch(() => {});
      }, 4000);
    },
  };
}
