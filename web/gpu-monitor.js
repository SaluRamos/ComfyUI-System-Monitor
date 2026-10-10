import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const PREFIX = "GPUMonitor";
let panel, list, status, toggle, timer, intervalSlider, intervalLabel;
let busy = false;
let collapsed = false;

function setting(name, fallback) {
    return app.ui.settings.getSettingValue(`${PREFIX}.${name}`) ?? fallback;
}

function refreshInterval() {
    const value = Number(setting("Interval", 2));
    return Number.isFinite(value) ? Math.min(5, Math.max(0.1, value)) : 2;
}

function syncInterval() {
    if (!intervalSlider) return;
    const value = refreshInterval();
    intervalSlider.value = String(value);
    intervalLabel.textContent = `Atualização: ${value.toFixed(1).replace(".", ",")} s`;
}

function positionPanel() {
    if (!panel) return;
    const position = setting("Position", "bottom-left");
    panel.dataset.position = position;
    panel.hidden = !setting("Enabled", true);
}

function processingRow(parent, label, value) {
    const percent = Number.isFinite(value) ? Math.min(100, Math.max(0, value)) : null;
    const text = document.createElement("div");
    text.className = "gpu-monitor-memory";
    text.textContent = `${label}: ${percent === null ? "— · indisponível" : `${percent.toFixed(0)}%`}`;
    const track = document.createElement("div");
    track.className = "gpu-monitor-track";
    const fill = document.createElement("div");
    fill.className = "gpu-monitor-fill";
    fill.style.width = `${percent ?? 0}%`;
    fill.dataset.level = percent >= 90 ? "high" : percent >= 75 ? "medium" : "normal";
    track.append(fill);
    parent.append(text, track);
}

function render(snapshot) {
    list.replaceChildren();
    const cpu = document.createElement("div");
    cpu.className = "gpu-monitor-row";
    processingRow(cpu, "CPU · Processamento", snapshot.cpu?.utilization_percent);
    list.append(cpu);
    const rows = snapshot.ram ? [{ ...snapshot.ram, name: "RAM do sistema", memory_type: "RAM" }, ...snapshot.gpus] : snapshot.gpus;
    for (const gpu of rows) {
        const row = document.createElement("div");
        row.className = "gpu-monitor-row";
        const name = document.createElement("div");
        name.className = "gpu-monitor-name";
        name.textContent = gpu.memory_type === "RAM" ? gpu.name : `GPU ${gpu.index} · ${gpu.name}`;
        name.title = name.textContent;
        const memory = document.createElement("div");
        memory.className = "gpu-monitor-memory";
        const valid = Number.isFinite(gpu.used_bytes) && Number.isFinite(gpu.total_bytes) && gpu.total_bytes > 0;
        const percent = valid ? Math.min(100, Math.max(0, gpu.used_bytes / gpu.total_bytes * 100)) : null;
        const gib = (bytes) => Number.isFinite(bytes) ? (bytes / 1024 ** 3).toFixed(2) : "—";
        memory.textContent = `${gpu.memory_type ?? "VRAM"}: ${gib(gpu.used_bytes)} / ${gib(gpu.total_bytes)} GiB${valid ? ` · ${percent.toFixed(0)}%` : " · indisponível"}`;
        const track = document.createElement("div");
        track.className = "gpu-monitor-track";
        const fill = document.createElement("div");
        fill.className = "gpu-monitor-fill";
        fill.style.width = `${percent ?? 0}%`;
        fill.dataset.level = percent >= 90 ? "high" : percent >= 75 ? "medium" : "normal";
        track.append(fill);
        row.append(name);
        if (gpu.memory_type !== "RAM") processingRow(row, "Processamento", gpu.utilization_percent);
        row.append(memory, track);
        list.append(row);
    }
    status.textContent = snapshot.errors.join(" ");
    status.hidden = !status.textContent;
    panel.dataset.stale = "false";
}

async function refresh() {
    clearTimeout(timer);
    if (busy || document.hidden || !setting("Enabled", true)) return;
    busy = true;
    const started = performance.now();
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 7000);
    try {
        const response = await api.fetchApi("/gpu-monitor/status", { signal: controller.signal, cache: "no-store" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const snapshot = await response.json();
        if (!Array.isArray(snapshot.gpus) || !Array.isArray(snapshot.errors)) throw new Error("Resposta inválida");
        render(snapshot);
    } catch (error) {
        panel.dataset.stale = "true";
        status.hidden = false;
        status.textContent = "Sem conexão com o monitor. Aguardando nova leitura…";
    } finally {
        clearTimeout(timeout);
        busy = false;
        if (!document.hidden && setting("Enabled", true)) {
            timer = setTimeout(refresh, Math.max(0, refreshInterval() * 1000 - (performance.now() - started)));
        }
    }
}

app.registerExtension({
    name: "ComfyUI.GPUMonitor",
    settings: [
        { id: `${PREFIX}.Enabled`, name: "Mostrar monitor do sistema", type: "boolean", defaultValue: true,
          category: ["GPU Monitor", "Painel", "Visibilidade"],
          onChange() { positionPanel(); if (panel) refresh(); } },
        { id: `${PREFIX}.Position`, name: "Canto da tela", type: "combo", defaultValue: "bottom-left",
          category: ["GPU Monitor", "Painel", "Posição"],
          options: [{ text: "Inferior direito", value: "bottom-right" }, { text: "Inferior esquerdo", value: "bottom-left" },
                    { text: "Superior direito", value: "top-right" }, { text: "Superior esquerdo", value: "top-left" }],
          onChange: positionPanel },
        { id: `${PREFIX}.Interval`, name: "Atualizar a cada (segundos)", type: "slider", defaultValue: 2,
          category: ["GPU Monitor", "Painel", "Atualização"], attrs: { min: 0.1, max: 5, step: 0.1 },
          onChange() { syncInterval(); if (panel) refresh(); } },
    ],
    setup() {
        if (document.getElementById("gpu-monitor-panel")) return;
        const stylesheet = document.createElement("link");
        stylesheet.rel = "stylesheet";
        stylesheet.href = new URL("./gpu-monitor.css", import.meta.url).href;
        document.head.append(stylesheet);
        panel = document.createElement("section");
        panel.id = "gpu-monitor-panel";
        panel.setAttribute("aria-label", "Monitor de processamento de CPU e GPUs, RAM e VRAM");
        const header = document.createElement("div");
        header.className = "gpu-monitor-header";
        const title = document.createElement("strong");
        title.textContent = "System Monitor";
        toggle = document.createElement("button");
        toggle.type = "button";
        toggle.textContent = "−";
        toggle.title = "Recolher painel";
        toggle.setAttribute("aria-expanded", "true");
        toggle.addEventListener("click", () => {
            collapsed = !collapsed;
            list.hidden = collapsed;
            toggle.textContent = collapsed ? "+" : "−";
            toggle.title = collapsed ? "Expandir painel" : "Recolher painel";
            toggle.setAttribute("aria-expanded", String(!collapsed));
        });
        header.append(title, toggle);
        list = document.createElement("div");
        list.className = "gpu-monitor-list";
        status = document.createElement("div");
        status.className = "gpu-monitor-status";
        status.textContent = "Consultando sistema…";
        const intervalControl = document.createElement("div");
        intervalControl.className = "gpu-monitor-interval";
        intervalLabel = document.createElement("label");
        intervalLabel.htmlFor = "gpu-monitor-interval";
        intervalSlider = document.createElement("input");
        intervalSlider.id = "gpu-monitor-interval";
        intervalSlider.type = "range";
        intervalSlider.min = "0.1";
        intervalSlider.max = "5";
        intervalSlider.step = "0.1";
        intervalSlider.addEventListener("input", () => {
            intervalLabel.textContent = `Atualização: ${Number(intervalSlider.value).toFixed(1).replace(".", ",")} s`;
            app.ui.settings.setSettingValue(`${PREFIX}.Interval`, Number(intervalSlider.value));
        });
        intervalControl.append(intervalLabel, intervalSlider);
        syncInterval();
        const actions = document.createElement("div");
        actions.className = "gpu-monitor-actions";
        const unload = document.createElement("button");
        unload.type = "button";
        unload.className = "gpu-monitor-unload";
        unload.textContent = "Unload all models";
        unload.title = "Descarregar todos os modelos e liberar o cache. Aguarda o workflow atual terminar.";
        const unloadStatus = document.createElement("div");
        unloadStatus.className = "gpu-monitor-status";
        unloadStatus.setAttribute("role", "status");
        unloadStatus.hidden = true;
        unload.addEventListener("click", async () => {
            if (unload.disabled) return;
            unload.disabled = true;
            unload.textContent = "Solicitando…";
            unloadStatus.hidden = true;
            try {
                const response = await api.fetchApi("/free", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ unload_models: true, free_memory: true }),
                });
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                unloadStatus.textContent = "Liberação solicitada. Se houver execução ativa, aguarda sua conclusão.";
                refresh();
            } catch (error) {
                unloadStatus.textContent = "Falha ao solicitar liberação. Tente novamente.";
            } finally {
                unload.disabled = false;
                unload.textContent = "Unload all models";
                unloadStatus.hidden = false;
            }
        });
        actions.append(unload, unloadStatus);
        panel.append(header, list, status, actions, intervalControl);
        document.body.append(panel);
        for (const event of ["pointerdown", "pointerup", "wheel", "dblclick"]) {
            panel.addEventListener(event, (e) => e.stopPropagation());
        }
        document.addEventListener("visibilitychange", () => {
            if (document.hidden) clearTimeout(timer);
            else refresh();
        });
        window.addEventListener("pagehide", () => clearTimeout(timer));
        positionPanel();
        refresh();
    },
});

