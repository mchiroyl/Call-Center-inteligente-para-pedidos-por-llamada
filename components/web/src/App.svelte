<script lang="ts">
  import { onMount } from "svelte";

  type MenuItem = {
    id: string;
    name: string;
    category: string;
    stock: number;
    price_cents: number;
  };

  type CartLine = {
    product_id: string;
    name: string;
    quantity: number;
    line_total_cents: number;
  };

  type OrderItem = {
    product_id: string;
    product_name: string;
    quantity: number;
    unit_price_cents: number;
  };

  type Order = {
    id: string;
    status: "nuevo" | "en_preparacion" | "listo";
    created_at: string;
    items: OrderItem[];
    total_cents: number;
    payment_method: "efectivo" | "tarjeta" | null;
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

  type ToolEnvelope = {
    kind: "cart" | "menu" | "order" | "error" | "payment_complete";
    message: string;
    lines?: CartLine[];
    menu?: MenuItem[];
    order?: { order_id: string; status: string; summary: string };
    total_cents?: number;
    error?: string;
  };

  const statusLabels = {
    nuevo: "Nuevo",
    en_preparacion: "En preparación",
    listo: "Listo",
  };

  const statusIcons = {
    nuevo: "🔴",
    en_preparacion: "🟡",
    listo: "🟢",
  };

  let route = $state("/");
  let menu = $state<MenuItem[]>([]);
  let cartLines = $state<CartLine[]>([]);
  let cartTotal = $state(0);
  let orders = $state<Order[]>([]);
  let kitchenConnected = $state(false);
  let voiceConnected = $state(false);
  let listening = $state(false);
  let voiceStarting = $state(false); // true while mic/WS is initializing (prevents double-click)
  let transcript = $state("");
  let assistantText = $state("Presiona el micrófono y pide tu sandwich.");
  let voiceStatus = $state("listo");
  let lastOrder = $state("");
  let lastPayment = $state("");
  let newOrderAlert = $state<string | null>(null);
  let alertTimer: ReturnType<typeof setTimeout> | undefined;

  let kitchenWs: WebSocket | undefined;
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
  let orderCompleted = false;
  let voiceServerReady = false;   // true once server sends {type:"ready"}
  let closeAfterOrderTimer: ReturnType<typeof setTimeout> | undefined;

  const targetInputSamples = 1600;

  const microphoneWorklet = `
    /**
     * MicrophoneProcessor — passthrough sin VAD.
     *
     * El browser ya aplica noiseSuppression y echoCancellation a nivel de
     * MediaStream; no necesitamos filtrar manualmente aquí.  Enviar siempre
     * el audio garantiza que AssemblyAI no cierre la conexión por inactividad
     * y que voces bajas o micrófonos poco sensibles sean siempre escuchados.
     */
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
  const orderTime = (raw: string) =>
    new Intl.DateTimeFormat("es-GT", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    }).format(new Date(raw));

  function syncRoute(): void {
    route = window.location.pathname === "/kitchen" ? "/kitchen" : "/";
  }

  async function loadMenu(): Promise<void> {
    const res = await fetch("/api/menu");
    menu = await res.json();
  }

  function wsUrl(path: string): string {
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    return `${protocol}://${window.location.host}${path}`;
  }

  function upsertOrder(order: Order): void {
    const without = orders.filter((item) => item.id !== order.id);
    orders = [order, ...without].sort(
      (a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
    );
  }

  /** Plays a brief beep using the Web Audio API — no external file needed */
  function playNewOrderBeep(): void {
    try {
      const ctx = new AudioContext();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.type = "sine";
      osc.frequency.setValueAtTime(880, ctx.currentTime);
      osc.frequency.setValueAtTime(1100, ctx.currentTime + 0.12);
      osc.frequency.setValueAtTime(880, ctx.currentTime + 0.24);
      gain.gain.setValueAtTime(0.4, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.5);
      osc.start(ctx.currentTime);
      osc.stop(ctx.currentTime + 0.5);
      osc.onended = () => ctx.close();
    } catch {
      // silent fail if AudioContext is blocked
    }
  }

  function triggerNewOrderAlert(order: Order): void {
    playNewOrderBeep();
    if (alertTimer) clearTimeout(alertTimer);
    newOrderAlert = `¡Nuevo pedido #${shortId(order.id)}!`;
    alertTimer = setTimeout(() => {
      newOrderAlert = null;
    }, 4000);
  }

  async function loadKitchenOrders(): Promise<void> {
    const res = await fetch("/api/kitchen/orders");
    orders = await res.json();
  }

  function connectKitchen(): void {
    kitchenWs?.close();
    kitchenWs = new WebSocket(wsUrl("/kitchen/ws"));
    kitchenWs.onopen = () => {
      kitchenConnected = true;
    };
    kitchenWs.onclose = () => {
      kitchenConnected = false;
      setTimeout(() => {
        if (route === "/kitchen") connectKitchen();
      }, 1500);
    };
    kitchenWs.onmessage = (event) => {
      const payload = JSON.parse(event.data);
      if (payload.type === "snapshot") orders = payload.orders;
      if (payload.type === "order_created") {
        upsertOrder(payload.order);
        triggerNewOrderAlert(payload.order);
      }
      if (payload.type === "order_updated") upsertOrder(payload.order);
    };
  }

  async function setStatus(order: Order, status: Order["status"]): Promise<void> {
    const res = await fetch(`/api/kitchen/orders/${order.id}/status`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    });
    if (res.ok) upsertOrder(await res.json());
  }

  function downsampleTo16k(input: Float32Array, sourceRate: number): Int16Array {
    if (sourceRate === 16000) {
      return floatToInt16(input);
    }
    const ratio = sourceRate / 16000;
    const length = Math.floor(input.length / ratio);
    const output = new Float32Array(length);
    for (let i = 0; i < length; i += 1) {
      output[i] = input[Math.floor(i * ratio)] ?? 0;
    }
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
    // Don't send audio until the server has finished the greeting and sent "ready"
    if (!voiceServerReady) return;
    if (!voiceWs || voiceWs.readyState !== WebSocket.OPEN) return;
    if (pendingPcmSampleCount < targetInputSamples && !force) return;
    if (pendingPcmSampleCount === 0) return;

    // Concatenar todos los chunks en un solo array
    const all = new Int16Array(pendingPcmSampleCount);
    let off = 0;
    for (const chunk of pendingPcmChunks) {
      all.set(chunk, off);
      off += chunk.length;
    }
    pendingPcmChunks = [];
    pendingPcmSampleCount = 0;

    // AssemblyAI requiere chunks entre 50 ms (800 samples) y 1000 ms (16 000 samples).
    // Enviamos en trozos de targetInputSamples (1600 samples = 100 ms) como máximo.
    const maxChunk = targetInputSamples; // 100 ms @ 16 kHz
    const minSamples = 800;             // 50 ms @ 16 kHz
    
    let processed = 0;
    for (let i = 0; i < all.length; i += maxChunk) {
      const slice = all.slice(i, i + maxChunk);
      if (slice.length < minSamples) {
        // Guardar el resto en un nuevo chunk si no estamos forzando
        if (!force) {
          pendingPcmChunks = [slice];
          pendingPcmSampleCount = slice.length;
        } else {
          // Si forzamos (fin de grabación), lo enviamos aunque sea corto
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
    // Si no hay audio reproduciéndose todavía (el TTS aún no ha llegado),
    // esperamos 8 s para dar tiempo al LLM + Cartesia a generar el audio
    // de despedida. Cada tts_chunk que llegue reiniciará el timer con el
    // tiempo exacto restante.
    const delay = remainingAudioMs > 0 ? remainingAudioMs + 900 : 8000;
    closeAfterOrderTimer = setTimeout(() => {
      voiceStatus = "pedido finalizado";
      voiceWs?.close();
      voiceConnected = false;
      void inputContext?.close().catch(() => undefined);
      inputContext = undefined;
    }, delay);
  }

  function applyToolResult(result: string): void {
    try {
      const payload = JSON.parse(result) as ToolEnvelope;
      if (payload.kind === "menu" && payload.menu) menu = payload.menu;
      if (payload.kind === "cart") {
        cartLines = payload.lines ?? [];
        cartTotal = payload.total_cents ?? 0;
      }
      if (payload.kind === "order" && payload.order) {
        // El pedido fue confirmado — limpiar carrito pero mantener el micrófono abierto
        // para continuar con la pregunta de pago.
        cartLines = [];
        cartTotal = 0;
        lastOrder = `Pedido #${shortId(payload.order.order_id)} enviado a cocina.`;
        voiceStatus = "pedido en cocina — esperando método de pago";
        // No cerrar sesión aquí; el agente seguirá preguntando el método de pago.
      }
      if (payload.kind === "payment_complete" && payload.order) {
        // Pago procesado — detener el micrófono YA pero NO cerrar el
        // WebSocket todavía: el agente aún va a generar y leer la despedida.
        // El cierre se programa en agent_end / tts_chunk cuando ya se sabe
        // cuánto audio queda por reproducir.
        orderCompleted = true;
        const method = payload.order.summary ?? "efectivo";
        const total = payload.total_cents ?? 0;
        lastPayment = `Pago en ${method} de ${money(total)} confirmado.`;
        voiceStatus = "pago confirmado — despidiendo...";
        stopMicrophoneInput();
        // No llamar scheduleCloseAfterOrderAudio() aquí: el TTS todavía
        // no ha llegado y el timer de 900 ms cierra el WS antes del audio.
      }
      if (payload.kind === "error") {
        voiceStatus = payload.error ?? payload.message;
      }
    } catch {
      // Tool results that are not JSON are only shown in the event stream.
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
      voiceStatus = "procesando pedido";
    }
    if (event.type === "agent_chunk" && event.text) {
      assistantText += event.text;
    }
    if (event.type === "tool_result" && event.result) {
      applyToolResult(event.result);
    }
    if (event.type === "agent_end") {
      voiceStatus = orderCompleted
        ? "pedido finalizado"
        : "listo para seguir hablando";
      if (orderCompleted) scheduleCloseAfterOrderAudio();
    }
    if (event.type === "tts_chunk" && event.audio) {
      playPcmChunk(event.audio);
    }
    if (event.type === "ready") {
      voiceStatus = "habla ahora";
    }
    if (event.type === "error") {
      voiceStatus = event.message ?? "error en el agente de voz";
    }
  }

  function waitForVoiceSocketOpen(socket: WebSocket): Promise<void> {
    return new Promise((resolve, reject) => {
      socket.onopen = () => {
        voiceConnected = true;
        voiceStatus = "escucha activa — preparando agente";
        resolve();
      };
      socket.onerror = () => {
        voiceStatus = "no se pudo conectar con el agente de voz";
        reject(new Error("No se pudo conectar con el agente de voz."));
      };
    });
  }

  async function startVoice(): Promise<void> {
    if (listening || voiceStarting) return;  // evitar doble inicio
    voiceStarting = true;

    // ── Reset state ──────────────────────────────────────────────────
    voiceStatus = "activando micrófono...";
    lastOrder = "";
    lastPayment = "";
    transcript = "";
    assistantText = "";
    orderCompleted = false;
    voiceServerReady = false;
    if (closeAfterOrderTimer) clearTimeout(closeAfterOrderTimer);

    // ── PASO 1: solicitar micrófono INMEDIATAMENTE ────────────────────
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
      voiceStatus = error instanceof Error ? error.message : "no se pudo acceder al micrófono";
      voiceStarting = false;
      return;
    }

    // ── PASO 2: iniciar AudioWorklet (captura activa, buffer acumula) ─
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
      // El audio se buferea. listening aun es false hasta que el WS conecte.
      voiceStatus = "conectando con el agente...";
    } catch (error) {
      await stopVoice();
      voiceStarting = false;
      voiceStatus = error instanceof Error ? error.message : "error al iniciar el audio";
      return;
    }

    // ── PASO 3: conectar WebSocket EN PARALELO al audio ───────────────
    voiceWs = new WebSocket(wsUrl("/ws"));
    voiceWs.binaryType = "arraybuffer";

    let rejectReady: ((reason?: unknown) => void) | undefined;
    voiceWs.onclose = () => {
      voiceConnected = false;
      listening = false;
      voiceStarting = false;
      voiceServerReady = false;
      rejectReady?.(new Error("La conexión de voz se cerró antes de iniciar."));
      rejectReady = undefined;
      if (orderCompleted) {
        voiceStatus = "pedido finalizado";
      } else if (voiceStatus !== "micrófono detenido") {
        voiceStatus = "conexión cerrada";
      }
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
          flushPendingInputAudio(true); // Enviar el audio acumulado en trozos válidos
          
          listening = true;
          voiceStarting = false;
          voiceStatus = "habla ahora";
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
      voiceStatus =
        error instanceof Error ? error.message : "no se pudo conectar con el agente";
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
    voiceStatus = "micrófono detenido";
    if (closeAfterOrderTimer) clearTimeout(closeAfterOrderTimer);
  }

  // Derived counts for kitchen header
  const countNuevo = $derived(orders.filter((o) => o.status === "nuevo").length);
  const countPrep = $derived(orders.filter((o) => o.status === "en_preparacion").length);
  const countListo = $derived(orders.filter((o) => o.status === "listo").length);

  onMount(() => {
    syncRoute();
    window.addEventListener("popstate", syncRoute);
    if (route === "/kitchen") {
      loadKitchenOrders();
      connectKitchen();
    } else {
      loadMenu();
    }

    return () => {
      window.removeEventListener("popstate", syncRoute);
      kitchenWs?.close();
      stopVoice();
    };
  });
</script>

{#if route === "/kitchen"}
  <!-- ═══════════════════════════════════════════════════════
       KITCHEN DISPLAY SYSTEM  /kitchen
  ════════════════════════════════════════════════════════════ -->
  <div class="kds-root">
    <!-- ── Toast alert para pedido nuevo ── -->
    {#if newOrderAlert}
      <div class="kds-toast" role="alert" aria-live="assertive">
        <span class="kds-toast-icon">🔔</span>
        <span>{newOrderAlert}</span>
      </div>
    {/if}

    <!-- ── Header ── -->
    <header class="kds-header">
      <div class="kds-header-left">
        <div class="kds-logo">
          <svg width="36" height="36" viewBox="0 0 64 64" aria-hidden="true">
            <rect width="64" height="64" rx="14" fill="#1f2a20"/>
            <path d="M16 26h32a7 7 0 0 1 0 14H16a7 7 0 0 1 0-14Z" fill="#f5f2ec"/>
            <path d="M18 24c3-6 9-9 14-9s11 3 14 9H18Z" fill="#d09b21"/>
            <path d="M18 40h28c-3 6-9 9-14 9s-11-3-14-9Z" fill="#c95830"/>
          </svg>
        </div>
        <div>
          <p class="kds-eyebrow">Kitchen Display System</p>
          <h1 class="kds-title">Pedidos en Cocina</h1>
        </div>
      </div>

      <div class="kds-header-right">
        <!-- Stats por estado -->
        <div class="kds-stats">
          <div class="kds-stat kds-stat--nuevo">
            <span class="kds-stat-count">{countNuevo}</span>
            <span class="kds-stat-label">Nuevos</span>
          </div>
          <div class="kds-stat kds-stat--prep">
            <span class="kds-stat-count">{countPrep}</span>
            <span class="kds-stat-label">En prep.</span>
          </div>
          <div class="kds-stat kds-stat--listo">
            <span class="kds-stat-count">{countListo}</span>
            <span class="kds-stat-label">Listos</span>
          </div>
        </div>
        <!-- Indicador de conexión -->
        <span class="kds-connection" class:kds-connection--online={kitchenConnected}>
          <span class="kds-dot"></span>
          {kitchenConnected ? "Tiempo real activo" : "Reconectando…"}
        </span>
        <!-- Link a la pantalla de pedidos -->
        <a class="kds-nav-link" href="/" aria-label="Ir a la pantalla de voz">
          ← Ordenar
        </a>
      </div>
    </header>

    <!-- ── Contenido ── -->
    <main class="kds-main">
      {#if orders.length === 0}
        <div class="kds-empty">
          <div class="kds-empty-icon">🍽️</div>
          <p>No hay pedidos confirmados todavía.</p>
          <p class="kds-empty-sub">Los pedidos aparecerán aquí en tiempo real cuando los clientes ordenen.</p>
        </div>
      {:else}
        <div class="kds-grid">
          {#each orders as order (order.id)}
            <article class="kds-card kds-card--{order.status}" id="order-{shortId(order.id)}">
              <!-- Cabecera de la tarjeta -->
              <div class="kds-card-head">
                <div class="kds-card-meta">
                  <p class="kds-order-id">#{shortId(order.id)}</p>
                  <p class="kds-order-time">🕐 {orderTime(order.created_at)}</p>
                </div>
                <span class="kds-badge kds-badge--{order.status}">
                  {statusIcons[order.status]}
                  {statusLabels[order.status]}
                </span>
              </div>

              <!-- Ítems del pedido -->
              <ul class="kds-items" aria-label="Productos del pedido">
                {#each order.items as item}
                  <li class="kds-item">
                    <span class="kds-item-qty">{item.quantity}×</span>
                    <span class="kds-item-name">{item.product_name}</span>
                    <span class="kds-item-price">{money(item.quantity * item.unit_price_cents)}</span>
                  </li>
                {/each}
              </ul>

              <!-- Total -->
              <div class="kds-total">
                <span>Total</span>
                <strong>{money(order.total_cents)}</strong>
              </div>

              <!-- Pago -->
              <div class="kds-payment-row">
                {#if order.payment_method === "tarjeta"}
                  <span class="kds-pay-badge kds-pay-badge--card">
                    💳 Pagado con tarjeta
                  </span>
                {:else if order.payment_method === "efectivo"}
                  <span class="kds-pay-badge kds-pay-badge--cash">
                    💵 Cobrar {money(order.total_cents)}
                  </span>
                {:else}
                  <span class="kds-pay-badge kds-pay-badge--pending">
                    ⏳ Pago pendiente
                  </span>
                {/if}
              </div>

              <div class="kds-actions" role="group" aria-label="Cambiar estado del pedido">
                <button
                  id="btn-nuevo-{shortId(order.id)}"
                  class="kds-action-btn kds-action-btn--nuevo"
                  class:kds-action-btn--active={order.status === "nuevo"}
                  onclick={() => setStatus(order, "nuevo")}
                  aria-pressed={order.status === "nuevo"}
                >
                  🔴 Nuevo
                </button>
                <button
                  id="btn-prep-{shortId(order.id)}"
                  class="kds-action-btn kds-action-btn--prep"
                  class:kds-action-btn--active={order.status === "en_preparacion"}
                  onclick={() => setStatus(order, "en_preparacion")}
                  aria-pressed={order.status === "en_preparacion"}
                >
                  🟡 En prep.
                </button>
                <button
                  id="btn-listo-{shortId(order.id)}"
                  class="kds-action-btn kds-action-btn--listo"
                  class:kds-action-btn--active={order.status === "listo"}
                  onclick={() => setStatus(order, "listo")}
                  aria-pressed={order.status === "listo"}
                >
                  🟢 Listo
                </button>
              </div>
            </article>
          {/each}
        </div>
      {/if}
    </main>
  </div>

{:else}
  <!-- ═══════════════════════════════════════════════════════
       VOICE ORDERING PAGE  /
  ════════════════════════════════════════════════════════════ -->
  <main class="voice-page">
    <header class="topbar">
      <div>
        <p class="eyebrow">Voice Sandwich Demo</p>
        <h1>Ordena hablando</h1>
      </div>
      <a class="kitchen-link" href="/kitchen" id="link-kitchen">Abrir /kitchen →</a>
    </header>

    <section class="voice-layout">
      <article class="voice-panel">
        <button
          id="btn-mic"
          class:recording={listening}
          class:connecting={voiceStarting}
          class="mic-button"
          disabled={voiceStarting}
          onclick={() => (listening ? stopVoice() : startVoice())}
          aria-label={listening ? "Detener grabación" : voiceStarting ? "Conectando..." : "Comenzar a hablar"}
        >
          {listening ? "Detener" : voiceStarting ? "..." : "Hablar"}
        </button>
        <p class="voice-state">
          {voiceConnected ? "agente conectado" : "agente desconectado"} — {voiceStatus}
        </p>
        <div class="conversation">
          <div>
            <p class="label">Cliente</p>
            <p>{transcript || "Aún no hay transcripción."}</p>
          </div>
          <div>
            <p class="label">Agente</p>
            <p>{assistantText || "Esperando respuesta del agente."}</p>
          </div>
          {#if lastOrder}
            <p class="notice">{lastOrder}</p>
          {/if}
          {#if lastPayment}
            <p class="notice notice--payment">💳 {lastPayment}</p>
          {/if}
        </div>
      </article>

      <aside class="cart voice-cart">
        <h2>Pedido detectado</h2>
        {#if cartLines.length === 0}
          <p class="muted">El pedido se llenará automáticamente cuando hables.</p>
        {:else}
          <ul class="items">
            {#each cartLines as line}
              <li>
                <span>{line.quantity} x {line.name}</span>
                <span>{money(line.line_total_cents)}</span>
              </li>
            {/each}
          </ul>
        {/if}
        <div class="total">
          <span>Total</span>
          <strong>{money(cartTotal)}</strong>
        </div>
      </aside>
    </section>

    <section class="menu-strip">
      {#each menu as item}
        <article class="menu-chip">
          <strong>{item.name}</strong>
          <span>{money(item.price_cents)} - stock {item.stock}</span>
        </article>
      {/each}
    </section>
  </main>
{/if}
