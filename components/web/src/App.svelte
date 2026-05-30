<script lang="ts">
  import { onMount } from "svelte";

  type MenuItem = {
    id: string;
    name: string;
    category: string;
    description: string;
    stock: number;
    price_cents: number;
    keywords: string[];
  };

  type DraftItem = {
    product_id: string;
    name: string;
    quantity: number;
    unit_price_cents: number;
  };

  type DraftOrder = {
    customer_name: string | null;
    customer_phone: string | null;
    delivery_type: "delivery" | "pickup" | null;
    address: string | null;
    payment_method: "efectivo" | "tarjeta" | "transferencia" | "paypal" | null;
    notes: string | null;
    items: DraftItem[];
    missing_fields: string[];
    ready_for_confirmation: boolean;
    last_retrieval_hits: RetrievalHit[];
  };

  type RetrievalHit = {
    id: string;
    title: string;
    source: string;
    content: string;
    score: number;
  };

  type OrderItem = {
    product_id: string;
    product_name: string;
    quantity: number;
    unit_price_cents: number;
  };

  type Order = {
    id: string;
    customer_name: string | null;
    customer_phone: string | null;
    delivery_type: "delivery" | "pickup";
    address: string | null;
    notes: string | null;
    payment_method: "efectivo" | "tarjeta" | "transferencia" | "paypal";
    payment_status: "paid" | "pending_cash";
    status: "nuevo" | "en_preparacion" | "listo" | "en_camino" | "entregado" | "cancelado";
    created_at: string;
    total_cents: number;
    items: OrderItem[];
    summary: string;
    tracking_code?: string;
  };

  type StaffUser = {
    username: string;
    display_name: string;
    role: "admin" | "cocina" | "caja" | "operaciones";
  };

  type VoiceEvent = {
    type: string;
    transcript?: string;
    text?: string;
    name?: string;
    result?: string;
    audio?: string;
    message?: string;
  };

  type ToolEnvelope =
    | { kind: "draft"; message: string; draft: DraftOrder; total_cents: number }
    | { kind: "retrieval"; message: string; query: string; hits: RetrievalHit[] }
    | { kind: "order"; message: string; order: Order; total_cents: number }
    | { kind: "error"; message: string; error: string };

  const emptyDraft: DraftOrder = {
    customer_name: null,
    customer_phone: null,
    delivery_type: null,
    address: null,
    payment_method: null,
    notes: null,
    items: [],
    missing_fields: ["items", "customer_name", "customer_phone", "delivery_type", "payment_method"],
    ready_for_confirmation: false,
    last_retrieval_hits: [],
  };

  const statusLabels = {
    nuevo: "Nuevo",
    en_preparacion: "En preparacion",
    listo: "Listo",
    en_camino: "En camino",
    entregado: "Entregado",
    cancelado: "Cancelado",
  };

  let route = $state("/");
  let menu = $state<MenuItem[]>([]);
  let draft = $state<DraftOrder>(emptyDraft);
  let draftTotal = $state(0);
  let retrievalHits = $state<RetrievalHit[]>([]);
  let orders = $state<Order[]>([]);
  let operationsConnected = $state(false);
  let voiceConnected = $state(false);
  let listening = $state(false);
  let voiceStarting = $state(false);
  let transcript = $state("");
  let assistantText = $state("Presione hablar para simular una llamada de pedidos.");
  let sttHistory = $state<{ text: string; type: "final"; time: string }[]>([]);
  let voiceStatus = $state("listo");
  let lastConfirmedOrder = $state<Order | null>(null);
  let currentUser = $state<StaffUser | null>(null);

  let operationsWs: WebSocket | undefined;
  let voiceWs: WebSocket | undefined;
  let mediaStream: MediaStream | undefined;
  let inputContext: AudioContext | undefined;
  let workletNode: AudioWorkletNode | undefined;
  let source: MediaStreamAudioSourceNode | undefined;
  let outputContext: AudioContext | undefined;
  let workletUrl: string | undefined;
  let nextAudioTime = 0;
  let pendingPcmChunks: Int16Array[] = [];
  let pendingPcmSampleCount = 0;
  let voiceServerReady = false;
  let orderCompleted = false;
  let closeAfterOrderTimer: ReturnType<typeof setTimeout> | undefined;

  const targetInputSamples = 1600;

  const microphoneWorklet = `
    class MicrophoneProcessor extends AudioWorkletProcessor {
      constructor() {
        super();
        this.bufferSize = 4096;
        this.buffer = new Float32Array(this.bufferSize);
        this.offset = 0;
      }
      process(inputs) {
        const input = inputs[0] && inputs[0][0];
        if (input && input.length > 0) {
          for (let i = 0; i < input.length; i++) {
            this.buffer[this.offset++] = input[i];
            if (this.offset >= this.bufferSize) {
              const copy = new Float32Array(this.bufferSize);
              copy.set(this.buffer);
              this.port.postMessage(copy, [copy.buffer]);
              this.offset = 0;
            }
          }
        }
        return true;
      }
    }
    registerProcessor('microphone-processor', MicrophoneProcessor);
  `;

  const money = (cents: number) => `Q ${(cents / 100).toFixed(2)}`;
  const shortId = (id: string) => id.slice(0, 8).toUpperCase();
  const formatTime = (raw: string) =>
    new Intl.DateTimeFormat("es-GT", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    }).format(new Date(raw));

  function syncRoute(): void {
    route =
      window.location.pathname === "/kitchen" || window.location.pathname === "/operations"
        ? "/operations"
        : "/";
  }

  function wsUrl(path: string): string {
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    return `${protocol}://${window.location.host}${path}`;
  }

  function getOperationsKey(): string {
    return window.sessionStorage.getItem("operations_api_key") || "";
  }

  function operationsHeaders(): HeadersInit {
    const key = getOperationsKey();
    return key ? { "X-Operations-Key": key } : {};
  }

  async function loadMenu(): Promise<void> {
    const res = await fetch("/api/menu");
    menu = await res.json();
  }

  async function loadOrders(): Promise<void> {
    const res = await fetch("/api/kitchen/orders", {
      headers: operationsHeaders(),
    });
    if (res.status === 401) {
      window.location.href = "/login?next=/operations";
      return;
    }
    if (!res.ok) throw new Error("No se pudo cargar el dashboard operativo.");
    orders = await res.json();
  }

  function upsertOrder(order: Order): void {
    const without = orders.filter((item) => item.id !== order.id);
    orders = [order, ...without].sort(
      (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
    );
  }

  function connectOperations(): void {
    operationsWs?.close();
    const key = getOperationsKey();
    const suffix = key ? `?ops_key=${encodeURIComponent(key)}` : "";
    operationsWs = new WebSocket(wsUrl(`/kitchen/ws${suffix}`));
    operationsWs.onopen = () => {
      operationsConnected = true;
    };
    operationsWs.onclose = (event) => {
      operationsConnected = false;
      if (event.code === 4401) {
        window.location.href = "/login?next=/operations";
      }
      setTimeout(() => {
        if (route === "/operations") connectOperations();
      }, 1500);
    };
    operationsWs.onmessage = (event) => {
      const payload = JSON.parse(event.data);
      if (payload.type === "snapshot") orders = payload.orders;
      if (payload.type === "order_created") upsertOrder(payload.order);
      if (payload.type === "order_updated") upsertOrder(payload.order);
    };
  }

  async function setStatus(order: Order, status: Order["status"]): Promise<void> {
    const res = await fetch(`/api/kitchen/orders/${order.id}/status`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...operationsHeaders(),
      },
      body: JSON.stringify({ status }),
    });
    if (res.status === 401) {
      window.location.href = "/login?next=/operations";
      return;
    }
    if (res.ok) upsertOrder(await res.json());
  }

  async function setPaymentStatus(order: Order, payment_status: "pending_cash" | "paid"): Promise<void> {
    const res = await fetch(`/api/kitchen/orders/${order.id}/payment-status`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...operationsHeaders(),
      },
      body: JSON.stringify({ payment_status }),
    });
    if (res.status === 401) {
      window.location.href = "/login?next=/operations";
      return;
    }
    if (res.ok) upsertOrder(await res.json());
  }

  async function loadAuth(): Promise<void> {
    const res = await fetch("/api/auth/me");
    if (!res.ok) {
      currentUser = null;
      if (route === "/operations") window.location.href = "/login?next=/operations";
      return;
    }
    currentUser = await res.json();
  }

  async function logout(): Promise<void> {
    await fetch("/api/auth/logout", { method: "POST" });
    window.location.href = "/login";
  }

  function trackingCode(order: Order): string {
    return order.tracking_code || shortId(order.id);
  }

  const canSeeKitchen = $derived(
    currentUser ? ["admin", "cocina", "operaciones"].includes(currentUser.role) : false,
  );
  const canSeeCashier = $derived(
    currentUser ? ["admin", "caja", "operaciones"].includes(currentUser.role) : false,
  );
  const canManageOps = $derived(
    currentUser ? ["admin", "operaciones"].includes(currentUser.role) : false,
  );

  function allowedStatusButtons(order: Order): Order["status"][] {
    if (!currentUser) return [];
    if (currentUser.role === "admin" || currentUser.role === "operaciones") {
      return ["nuevo", "en_preparacion", "listo", "en_camino", "entregado", "cancelado"];
    }
    if (currentUser.role === "cocina") {
      if (order.status === "nuevo") return ["en_preparacion"];
      if (order.status === "en_preparacion") return ["listo"];
    }
    return [];
  }

  function downsampleTo16k(input: Float32Array, sourceRate: number): Int16Array {
    if (sourceRate === 16000) return floatToInt16(input);
    const ratio = sourceRate / 16000;
    const length = Math.floor(input.length / ratio);
    const output = new Float32Array(length);
    for (let i = 0; i < length; i += 1) output[i] = input[Math.floor(i * ratio)] ?? 0;
    return floatToInt16(output);
  }

  function floatToInt16(input: Float32Array): Int16Array {
    const output = new Int16Array(input.length);
    for (let i = 0; i < input.length; i += 1) {
      const sample = Math.max(-1, Math.min(1, input[i] ?? 0));
      output[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
    }
    return output;
  }

  function playPcmChunk(base64Audio: string): void {
    outputContext ??= new AudioContext({ sampleRate: 24000 });
    const binary = atob(base64Audio);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    const pcm = new Int16Array(bytes.buffer);
    const buffer = outputContext.createBuffer(1, pcm.length, 24000);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < pcm.length; i += 1) channel[i] = pcm[i] / 0x8000;
    const node = outputContext.createBufferSource();
    node.buffer = buffer;
    node.connect(outputContext.destination);
    nextAudioTime = Math.max(nextAudioTime, outputContext.currentTime);
    node.start(nextAudioTime);
    nextAudioTime += buffer.duration;
    if (orderCompleted) scheduleCloseAfterOrderAudio();
  }

  function flushPendingInputAudio(force = false): void {
    if (!voiceServerReady) return;
    if (!voiceWs || voiceWs.readyState !== WebSocket.OPEN) return;
    if (pendingPcmSampleCount < targetInputSamples && !force) return;
    if (pendingPcmSampleCount === 0) return;

    const all = new Int16Array(pendingPcmSampleCount);
    let offset = 0;
    for (const chunk of pendingPcmChunks) {
      all.set(chunk, offset);
      offset += chunk.length;
    }
    pendingPcmChunks = [];
    pendingPcmSampleCount = 0;

    const maxChunk = targetInputSamples;
    const minSamples = 800;

    let processed = 0;
    for (let i = 0; i < all.length; i += maxChunk) {
      const slice = all.slice(i, i + maxChunk);
      if (slice.length < minSamples) {
        if (!force) {
          pendingPcmChunks = [slice];
          pendingPcmSampleCount = slice.length;
        } else {
          voiceWs.send(slice);
        }
        break;
      }
      voiceWs.send(slice);
      processed += slice.length;
    }

    if (processed === all.length) {
      pendingPcmChunks = [];
      pendingPcmSampleCount = 0;
    }
  }

  function queueInputAudio(input: Float32Array): void {
    const pcm = downsampleTo16k(input, inputContext?.sampleRate ?? 48000);
    pendingPcmChunks.push(pcm);
    pendingPcmSampleCount += pcm.length;
    flushPendingInputAudio();
  }

  function stopMicrophoneInput(): void {
    flushPendingInputAudio(true);
    workletNode?.disconnect();
    workletNode?.port.close();
    source?.disconnect();
    mediaStream?.getTracks().forEach((track) => track.stop());
    if (workletUrl) URL.revokeObjectURL(workletUrl);
    workletNode = undefined;
    source = undefined;
    mediaStream = undefined;
    workletUrl = undefined;
    pendingPcmChunks = [];
    pendingPcmSampleCount = 0;
    listening = false;
  }

  function scheduleCloseAfterOrderAudio(): void {
    if (closeAfterOrderTimer) clearTimeout(closeAfterOrderTimer);
    const remainingAudioMs = outputContext
      ? Math.max(0, nextAudioTime - outputContext.currentTime) * 1000
      : 0;
    const delay = remainingAudioMs > 0 ? remainingAudioMs + 900 : 8000;
    closeAfterOrderTimer = setTimeout(() => {
      voiceStatus = "llamada finalizada";
      voiceWs?.close();
      voiceConnected = false;
      void inputContext?.close().catch(() => undefined);
      inputContext = undefined;
    }, delay);
  }

  function applyToolResult(result: string): void {
    try {
      const payload = JSON.parse(result) as ToolEnvelope;
      if (payload.kind === "draft") {
        draft = payload.draft;
        draftTotal = payload.total_cents;
        retrievalHits = payload.draft.last_retrieval_hits ?? retrievalHits;
      }
      if (payload.kind === "retrieval") {
        retrievalHits = payload.hits;
      }
      if (payload.kind === "order") {
        lastConfirmedOrder = payload.order;
        draft = emptyDraft;
        draftTotal = 0;
        upsertOrder(payload.order);
        orderCompleted = true;
        stopMicrophoneInput();
      }
      if (payload.kind === "error") {
        voiceStatus = payload.error;
      }
    } catch {
      // ignore non-JSON tool messages
    }
  }

  function addSttHistory(text: string): void {
    sttHistory = [
      ...sttHistory,
      { text, type: "final", time: new Date().toLocaleTimeString() },
    ];
    if (sttHistory.length > 20) {
      sttHistory = sttHistory.slice(-20);
    }
  }

  function handleVoiceEvent(event: VoiceEvent): void {
    if (event.type === "stt_chunk" && event.transcript) {
      transcript = event.transcript;
      voiceStatus = "escuchando";
    }
    if (event.type === "stt_output" && event.transcript) {
      transcript = event.transcript;
      assistantText = "";
      voiceStatus = "procesando";
      addSttHistory(event.transcript);
    }
    if (event.type === "agent_chunk" && event.text) {
      assistantText += event.text;
    }
    if (event.type === "tool_result" && event.result) {
      applyToolResult(event.result);
    }
    if (event.type === "agent_end") {
      voiceStatus = orderCompleted ? "pedido confirmado" : "listo para continuar";
      if (orderCompleted) scheduleCloseAfterOrderAudio();
    }
    if (event.type === "tts_chunk" && event.audio) {
      playPcmChunk(event.audio);
    }
    if (event.type === "ready") {
      voiceStatus = "hable ahora";
    }
    if (event.type === "error") {
      voiceStatus = event.message ?? "error en la llamada simulada";
    }
  }

  function waitForVoiceSocketOpen(socket: WebSocket): Promise<void> {
    return new Promise((resolve, reject) => {
      socket.onopen = () => {
        voiceConnected = true;
        voiceStatus = "inicializando llamada";
        resolve();
      };
      socket.onerror = () => {
        voiceStatus = "no se pudo conectar con el backend de voz";
        reject(new Error("No se pudo conectar con el backend de voz."));
      };
    });
  }

  async function startVoice(): Promise<void> {
    if (listening || voiceStarting) return;
    voiceStarting = true;
    voiceStatus = "activando microfono";
    transcript = "";
    assistantText = "";
    lastConfirmedOrder = null;
    orderCompleted = false;
    voiceServerReady = false;
    if (closeAfterOrderTimer) clearTimeout(closeAfterOrderTimer);

    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: false,
          sampleRate: 48000,
        },
      });
    } catch (error) {
      voiceStatus = error instanceof Error ? error.message : "no se pudo acceder al microfono";
      voiceStarting = false;
      return;
    }

    try {
      inputContext = new AudioContext();
      source = inputContext.createMediaStreamSource(mediaStream);
      workletUrl = URL.createObjectURL(
        new Blob([microphoneWorklet], { type: "text/javascript" }),
      );
      await inputContext.audioWorklet.addModule(workletUrl);
      workletNode = new AudioWorkletNode(inputContext, "microphone-processor", {
        numberOfInputs: 1,
        numberOfOutputs: 1,
        outputChannelCount: [1],
      });
      workletNode.port.onmessage = (event: MessageEvent<Float32Array>) => {
        queueInputAudio(event.data);
      };
      source.connect(workletNode);
      workletNode.connect(inputContext.destination);
      voiceStatus = "conectando llamada";
    } catch (error) {
      await stopVoice();
      voiceStarting = false;
      voiceStatus = error instanceof Error ? error.message : "error al iniciar el audio";
      return;
    }

    voiceWs = new WebSocket(wsUrl("/ws"));
    voiceWs.binaryType = "arraybuffer";

    let rejectReady: ((reason?: unknown) => void) | undefined;
    voiceWs.onclose = () => {
      voiceConnected = false;
      listening = false;
      voiceStarting = false;
      voiceServerReady = false;
      rejectReady?.(new Error("La llamada se cerro antes de iniciar."));
      rejectReady = undefined;
      if (orderCompleted) voiceStatus = "pedido confirmado";
      else if (voiceStatus !== "microfono detenido") voiceStatus = "conexion cerrada";
    };

    let markReady: (() => void) | undefined;
    const readyPromise = new Promise<void>((resolve, reject) => {
      markReady = resolve;
      rejectReady = reject;
    });

    voiceWs.onmessage = (event) => {
      if (typeof event.data === "string") {
        const payload = JSON.parse(event.data) as VoiceEvent;
        handleVoiceEvent(payload);
        if (payload.type === "ready") {
          voiceServerReady = true;
          flushPendingInputAudio(true);
          listening = true;
          voiceStarting = false;
          voiceStatus = "hable ahora";
          markReady?.();
          markReady = undefined;
          rejectReady = undefined;
        }
      }
    };

    try {
      await waitForVoiceSocketOpen(voiceWs);
      await readyPromise;
    } catch (error) {
      await stopVoice();
      voiceStarting = false;
      voiceStatus = error instanceof Error ? error.message : "no se pudo iniciar la llamada";
    }
  }

  async function stopVoice(): Promise<void> {
    stopMicrophoneInput();
    await inputContext?.close();
    voiceWs?.close();
    inputContext = undefined;
    listening = false;
    voiceStarting = false;
    voiceConnected = false;
    voiceServerReady = false;
    voiceStatus = "microfono detenido";
    if (closeAfterOrderTimer) clearTimeout(closeAfterOrderTimer);
  }

  const countNuevo = $derived(orders.filter((o) => o.status === "nuevo").length);
  const countPrep = $derived(orders.filter((o) => o.status === "en_preparacion").length);
  const countListo = $derived(orders.filter((o) => o.status === "listo").length);
  const countEnCamino = $derived(orders.filter((o) => o.status === "en_camino").length);
  const cashierQueue = $derived(
    orders.filter(
      (o) => o.payment_status === "pending_cash" && !["entregado", "cancelado"].includes(o.status),
    ),
  );

  onMount(() => {
    syncRoute();
    window.addEventListener("popstate", syncRoute);
    loadMenu();
    if (route === "/operations") {
      loadAuth().then(() => loadOrders()).catch(() => {
        operationsConnected = false;
      });
      connectOperations();
    }

    return () => {
      window.removeEventListener("popstate", syncRoute);
      operationsWs?.close();
      stopVoice();
    };
  });
</script>

{#if route === "/operations"}
  <main class="ops-page">
    <header class="hero hero--ops">
      <div>
        <p class="eyebrow">Call Center IA</p>
        <h1>Dashboard Operativo</h1>
        <p class="hero-copy">
          Pedidos activos, flujo de cocina y cola de caja sincronizados en tiempo real.
        </p>
      </div>
      <div class="hero-actions">
        {#if currentUser}
          <span class="status-pill">{currentUser.display_name} · {currentUser.role}</span>
          <button class="nav-link nav-link--button" onclick={() => logout()}>Cerrar sesion</button>
        {/if}
        <span class:status-online={operationsConnected} class="status-pill">
          {operationsConnected ? "Tiempo real activo" : "Reconectando"}
        </span>
        <a class="nav-link" href="/">Volver a la llamada</a>
      </div>
    </header>

    <section class="stats-grid">
      <article class="stat-card">
        <span>Nuevos</span>
        <strong>{countNuevo}</strong>
      </article>
      <article class="stat-card">
        <span>En preparacion</span>
        <strong>{countPrep}</strong>
      </article>
      <article class="stat-card">
        <span>Listos</span>
        <strong>{countListo}</strong>
      </article>
      <article class="stat-card">
        <span>En camino</span>
        <strong>{countEnCamino}</strong>
      </article>
      <article class="stat-card">
        <span>Caja pendiente</span>
        <strong>{cashierQueue.length}</strong>
      </article>
    </section>

    <section class="ops-columns">
      <article class="ops-column">
        <h2>Pedidos activos</h2>
        {#if orders.length === 0}
          <p class="muted">Todavia no hay ordenes confirmadas.</p>
        {:else}
          {#each orders.filter((order) => order.status !== "entregado" && order.status !== "cancelado") as order}
            <div class="order-card">
              <div class="order-head">
                <strong>#{shortId(order.id)}</strong>
                <span>{statusLabels[order.status]}</span>
              </div>
              <p>{order.summary}</p>
              <p class="muted">{order.delivery_type === "delivery" ? order.address : "Recoge en tienda"}</p>
              <p class="muted"><a class="inline-link" href={`/track/${trackingCode(order)}`}>Seguimiento cliente</a></p>
            </div>
          {/each}
        {/if}
      </article>

      {#if canSeeKitchen}
        <article class="ops-column">
          <h2>Modulo de cocina</h2>
          {#each orders.filter((order) => order.status !== "entregado" && order.status !== "cancelado") as order}
            <div class="order-card order-card--kitchen">
              <div class="order-head">
                <strong>#{shortId(order.id)}</strong>
                <span>{formatTime(order.created_at)}</span>
              </div>
              <ul class="item-list">
                {#each order.items as item}
                  <li>{item.quantity} x {item.product_name}</li>
                {/each}
              </ul>
              <div class="pill-row">
                {#each allowedStatusButtons(order) as status}
                  <button onclick={() => setStatus(order, status)}>{statusLabels[status]}</button>
                {/each}
              </div>
              <p class="muted"><a class="inline-link" href={`/track/${trackingCode(order)}`}>Abrir seguimiento</a></p>
            </div>
          {/each}
        </article>
      {/if}

      {#if canSeeCashier}
        <article class="ops-column">
          <h2>Modulo de caja</h2>
          {#if cashierQueue.length === 0}
            <p class="muted">No hay cobros pendientes en efectivo.</p>
          {:else}
            {#each cashierQueue as order}
              <div class="order-card order-card--cashier">
                <div class="order-head">
                  <strong>#{shortId(order.id)}</strong>
                  <span>{money(order.total_cents)}</span>
                </div>
                <p>Cobrar en efectivo al entregar o al retirar.</p>
                <p class="muted">{order.summary}</p>
                <div class="pill-row">
                  <button onclick={() => setPaymentStatus(order, "paid")}>Marcar pagado</button>
                </div>
              </div>
            {/each}
          {/if}
        </article>
      {/if}
    </section>
  </main>
{:else}
  <main class="voice-page">
    <header class="hero">
      <div>
        <p class="eyebrow">Proyecto Final IA</p>
        <h1>Call Center Inteligente para Pedidos</h1>
        <p class="hero-copy">
          Simulacion web de llamada con STT, structured output, RAG, tool calling y actualizacion operativa en tiempo real.
        </p>
      </div>
      <div class="hero-actions">
        <a class="nav-link" href="/login?next=/operations">Ingreso interno</a>
      </div>
    </header>

    <section class="voice-layout">
      <article class="panel panel--voice">
        <button
          class="mic-button"
          class:recording={listening}
          class:connecting={voiceStarting}
          disabled={voiceStarting}
          onclick={() => (listening ? stopVoice() : startVoice())}
          aria-label={listening ? "Detener llamada" : "Iniciar llamada"}
        >
          {listening ? "Detener" : voiceStarting ? "..." : "Hablar"}
        </button>

        <p class="status-line">
          {voiceConnected ? "backend conectado" : "backend desconectado"} - {voiceStatus}
        </p>

        <div class="conversation-card">
          <div>
            <span class="label">Cliente</span>
            <p>{transcript || "Aun no hay transcripcion."}</p>
          </div>
          <div>
            <span class="label">Agente</span>
            <p>{assistantText || "Esperando respuesta del agente."}</p>
          </div>
          <div class="stt-monitor">
            <span class="label">Historial de cliente</span>
            {#if sttHistory.length === 0}
              <p class="muted">No se ha recibido transcripción final.</p>
            {:else}
              <div class="stt-history">
                {#each sttHistory as entry}
                  <div class="stt-entry">
                    <span class="stt-time">{entry.time}</span>
                    <p>{entry.text}</p>
                  </div>
                {/each}
              </div>
            {/if}
          </div>
          {#if lastConfirmedOrder}
            <div class="alert-success">
              Orden #{shortId(lastConfirmedOrder.id)} confirmada por {money(lastConfirmedOrder.total_cents)}.
              <a class="inline-link" href={`/track/${shortId(lastConfirmedOrder.id)}`}>Ver seguimiento</a>
            </div>
          {/if}
        </div>
      </article>

      <article class="panel">
        <div class="panel-head">
          <h2>Borrador estructurado</h2>
          <span class:badge-ready={draft.ready_for_confirmation} class="badge">
            {draft.ready_for_confirmation ? "Listo para confirmar" : "En construccion"}
          </span>
        </div>

        <div class="draft-grid">
          <div><span class="label">Cliente</span><p>{draft.customer_name || "No capturado"}</p></div>
          <div><span class="label">Telefono</span><p>{draft.customer_phone || "No capturado"}</p></div>
          <div><span class="label">Modalidad</span><p>{draft.delivery_type || "Pendiente"}</p></div>
          <div><span class="label">Pago</span><p>{draft.payment_method || "Pendiente"}</p></div>
          <div class="draft-full"><span class="label">Direccion</span><p>{draft.address || "Pendiente"}</p></div>
        </div>

        <ul class="item-list">
          {#if draft.items.length === 0}
            <li class="muted">Todavia no hay productos reconocidos.</li>
          {:else}
            {#each draft.items as item}
              <li>{item.quantity} x {item.name} - {money(item.quantity * item.unit_price_cents)}</li>
            {/each}
          {/if}
        </ul>

        <div class="summary-row">
          <strong>Total detectado</strong>
          <strong>{money(draftTotal)}</strong>
        </div>

        <div>
          <span class="label">Campos pendientes</span>
          <p>{draft.missing_fields.length ? draft.missing_fields.join(", ") : "Ninguno"}</p>
        </div>
      </article>
    </section>

    <section class="detail-grid">
      <article class="panel">
        <h2>Contexto recuperado por RAG</h2>
        {#if retrievalHits.length === 0}
          <p class="muted">Los resultados semanticos apareceran aqui cuando el sistema consulte menu, pagos u horarios.</p>
        {:else}
          {#each retrievalHits as hit}
            <div class="retrieval-hit">
              <strong>{hit.title}</strong>
              <span>{hit.source} - score {hit.score.toFixed(2)}</span>
              <p>{hit.content}</p>
            </div>
          {/each}
        {/if}
      </article>

      <article class="panel">
        <h2>Menu indexado</h2>
        <div class="menu-grid">
          {#each menu as item}
            <div class="menu-card">
              <strong>{item.name}</strong>
              <span>{money(item.price_cents)}</span>
              <p>{item.description}</p>
              <small>Stock {item.stock} - {item.category}</small>
            </div>
          {/each}
        </div>
      </article>
    </section>
  </main>
{/if}
